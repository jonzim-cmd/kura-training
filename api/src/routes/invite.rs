use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{Duration, Utc};
use rand::Rng;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

// ──────────────────────────────────────────────
// Token generation
// ──────────────────────────────────────────────

fn generate_invite_token() -> String {
    let mut rng = rand::thread_rng();
    let hex: String = (0..32)
        .map(|_| format!("{:x}", rng.r#gen::<u8>() % 16))
        .collect();
    format!("kura_inv_{hex}")
}

// ──────────────────────────────────────────────
// POST /v1/access/request (public, no auth)
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct AccessRequestBody {
    pub email: String,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub context: Option<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AccessRequestResponse {
    pub status: String,
    pub message: String,
}

#[utoipa::path(
    post,
    path = "/v1/access/request",
    request_body = AccessRequestBody,
    responses(
        (status = 201, description = "Access request received", body = AccessRequestResponse),
        (status = 400, description = "Validation error", body = kura_core::error::ApiError),
    ),
    tag = "access"
)]
pub async fn submit_access_request(
    State(state): State<AppState>,
    Json(req): Json<AccessRequestBody>,
) -> Result<impl IntoResponse, AppError> {
    let email = req.email.trim().to_lowercase();
    if email.is_empty() || !email.contains('@') {
        return Err(AppError::Validation {
            message: "A valid email address is required.".to_string(),
            field: Some("email".to_string()),
            received: None,
            docs_hint: None,
        });
    }

    let name = req
        .name
        .as_deref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty());
    let context = req
        .context
        .as_deref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty());

    // Always return 201 even if duplicate (no info leak)
    sqlx::query(
        "INSERT INTO access_requests (email, name, context) VALUES ($1, $2, $3) \
         ON CONFLICT DO NOTHING",
    )
    .bind(&email)
    .bind(name)
    .bind(context)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    Ok((
        StatusCode::CREATED,
        Json(AccessRequestResponse {
            status: "received".to_string(),
            message: "We'll be in touch.".to_string(),
        }),
    ))
}

pub fn public_router() -> Router<AppState> {
    Router::new().route("/v1/access/request", post(submit_access_request))
}

// ──────────────────────────────────────────────
// Admin: list access requests
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct ListRequestsQuery {
    pub status: Option<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema, sqlx::FromRow)]
pub struct AccessRequestRow {
    pub id: Uuid,
    pub email: String,
    pub name: Option<String>,
    pub context: Option<String>,
    pub status: String,
    pub created_at: chrono::DateTime<Utc>,
    pub reviewed_at: Option<chrono::DateTime<Utc>>,
    pub invite_token_id: Option<Uuid>,
}

#[utoipa::path(
    get,
    path = "/v1/admin/access-requests",
    params(("status" = Option<String>, Query, description = "Filter by status: pending, approved, rejected")),
    responses(
        (status = 200, description = "List of access requests", body = Vec<AccessRequestRow>),
    ),
    security(("bearer_auth" = [])),
    tag = "admin"
)]
pub async fn list_access_requests(
    admin: AuthenticatedUser,
    State(state): State<AppState>,
    Query(query): Query<ListRequestsQuery>,
) -> Result<Json<serde_json::Value>, AppError> {
    ensure_admin(&state.db, admin.user_id).await?;

    let rows = if let Some(status) = &query.status {
        sqlx::query_as::<_, AccessRequestRow>(
            "SELECT id, email, name, context, status, created_at, reviewed_at, invite_token_id \
             FROM access_requests WHERE status = $1 ORDER BY created_at DESC",
        )
        .bind(status)
        .fetch_all(&state.db)
        .await
        .map_err(AppError::Database)?
    } else {
        sqlx::query_as::<_, AccessRequestRow>(
            "SELECT id, email, name, context, status, created_at, reviewed_at, invite_token_id \
             FROM access_requests ORDER BY created_at DESC",
        )
        .fetch_all(&state.db)
        .await
        .map_err(AppError::Database)?
    };

    Ok(Json(serde_json::json!({ "requests": rows })))
}

// ──────────────────────────────────────────────
// Admin: approve access request
// ──────────────────────────────────────────────

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct ApproveResponse {
    pub status: String,
    pub invite_token: String,
    pub invite_url: String,
    pub expires_at: chrono::DateTime<Utc>,
    pub email_sent: bool,
}

#[utoipa::path(
    post,
    path = "/v1/admin/access-requests/{id}/approve",
    responses(
        (status = 200, description = "Request approved, invite created", body = ApproveResponse),
        (status = 404, description = "Request not found"),
        (status = 409, description = "Request already processed"),
    ),
    security(("bearer_auth" = [])),
    tag = "admin"
)]
pub async fn approve_access_request(
    admin: AuthenticatedUser,
    State(state): State<AppState>,
    Path(request_id): Path<Uuid>,
) -> Result<Json<ApproveResponse>, AppError> {
    ensure_admin(&state.db, admin.user_id).await?;

    let mut tx = state.db.begin().await.map_err(AppError::Database)?;

    // Fetch and lock the request
    let row = sqlx::query_as::<_, (String, String)>(
        "SELECT email, status FROM access_requests WHERE id = $1 FOR UPDATE",
    )
    .bind(request_id)
    .fetch_optional(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    let (email, current_status) = match row {
        Some(r) => r,
        None => {
            return Err(AppError::NotFound {
                resource: format!("access_request {request_id}"),
            });
        }
    };

    if current_status != "pending" {
        return Err(AppError::Conflict {
            message: format!("Request already {current_status}"),
        });
    }

    // Generate invite token
    let token = generate_invite_token();
    let expires_at = Utc::now() + Duration::days(7);
    let token_id = Uuid::now_v7();

    sqlx::query(
        "INSERT INTO invite_tokens (id, token, email, created_by, expires_at) \
         VALUES ($1, $2, $3, $4, $5)",
    )
    .bind(token_id)
    .bind(&token)
    .bind(&email)
    .bind(admin.user_id)
    .bind(expires_at)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    // Update access request
    sqlx::query(
        "UPDATE access_requests SET status = 'approved', reviewed_at = NOW(), \
         reviewed_by = $1, invite_token_id = $2 WHERE id = $3",
    )
    .bind(admin.user_id)
    .bind(token_id)
    .bind(request_id)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    tx.commit().await.map_err(AppError::Database)?;

    let frontend_url =
        std::env::var("FRONTEND_URL").unwrap_or_else(|_| "https://kura.dev".to_string());
    let invite_url = format!("{frontend_url}/signup?invite={token}");

    // Send invite email
    let email_sent = send_invite_email(&email, &invite_url, &expires_at).await;

    Ok(Json(ApproveResponse {
        status: "approved".to_string(),
        invite_token: token,
        invite_url,
        expires_at,
        email_sent,
    }))
}

// ──────────────────────────────────────────────
// Admin: reject access request
// ──────────────────────────────────────────────

#[utoipa::path(
    post,
    path = "/v1/admin/access-requests/{id}/reject",
    responses(
        (status = 200, description = "Request rejected"),
        (status = 404, description = "Request not found"),
        (status = 409, description = "Request already processed"),
    ),
    security(("bearer_auth" = [])),
    tag = "admin"
)]
pub async fn reject_access_request(
    admin: AuthenticatedUser,
    State(state): State<AppState>,
    Path(request_id): Path<Uuid>,
) -> Result<Json<serde_json::Value>, AppError> {
    ensure_admin(&state.db, admin.user_id).await?;

    let result = sqlx::query(
        "UPDATE access_requests SET status = 'rejected', reviewed_at = NOW(), reviewed_by = $1 \
         WHERE id = $2 AND status = 'pending'",
    )
    .bind(admin.user_id)
    .bind(request_id)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    if result.rows_affected() == 0 {
        // Could be not found or already processed — check which
        let exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM access_requests WHERE id = $1)")
                .bind(request_id)
                .fetch_one(&state.db)
                .await
                .map_err(AppError::Database)?;

        if !exists {
            return Err(AppError::NotFound {
                resource: format!("access_request {request_id}"),
            });
        }
        return Err(AppError::Conflict {
            message: "Request already processed".to_string(),
        });
    }

    Ok(Json(serde_json::json!({ "status": "rejected" })))
}

// ──────────────────────────────────────────────
// Admin: create manual invite
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct CreateInviteRequest {
    #[serde(default)]
    pub email: Option<String>,
    #[serde(default = "default_expires_days")]
    pub expires_in_days: i64,
}

fn default_expires_days() -> i64 {
    7
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct InviteResponse {
    pub token: String,
    pub invite_url: String,
    pub expires_at: chrono::DateTime<Utc>,
}

#[utoipa::path(
    post,
    path = "/v1/admin/invites",
    request_body = CreateInviteRequest,
    responses(
        (status = 201, description = "Invite created", body = InviteResponse),
    ),
    security(("bearer_auth" = [])),
    tag = "admin"
)]
pub async fn create_invite(
    admin: AuthenticatedUser,
    State(state): State<AppState>,
    Json(req): Json<CreateInviteRequest>,
) -> Result<impl IntoResponse, AppError> {
    ensure_admin(&state.db, admin.user_id).await?;

    let email = req
        .email
        .as_deref()
        .map(|s| s.trim().to_lowercase())
        .filter(|s| !s.is_empty());
    let token = generate_invite_token();
    let days = req.expires_in_days.clamp(1, 90);
    let expires_at = Utc::now() + Duration::days(days);

    sqlx::query(
        "INSERT INTO invite_tokens (token, email, created_by, expires_at) VALUES ($1, $2, $3, $4)",
    )
    .bind(&token)
    .bind(&email)
    .bind(admin.user_id)
    .bind(expires_at)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    let frontend_url =
        std::env::var("FRONTEND_URL").unwrap_or_else(|_| "https://kura.dev".to_string());
    let invite_url = format!("{frontend_url}/signup?invite={token}");

    Ok((
        StatusCode::CREATED,
        Json(InviteResponse {
            token,
            invite_url,
            expires_at,
        }),
    ))
}

// ──────────────────────────────────────────────
// Admin: list invites
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct ListInvitesQuery {
    pub status: Option<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema, sqlx::FromRow)]
pub struct InviteRow {
    pub id: Uuid,
    pub token: String,
    pub email: Option<String>,
    pub created_at: chrono::DateTime<Utc>,
    pub expires_at: chrono::DateTime<Utc>,
    pub used_at: Option<chrono::DateTime<Utc>>,
    pub used_by: Option<Uuid>,
}

#[utoipa::path(
    get,
    path = "/v1/admin/invites",
    params(("status" = Option<String>, Query, description = "Filter: unused, used, expired")),
    responses(
        (status = 200, description = "List of invites", body = Vec<InviteRow>),
    ),
    security(("bearer_auth" = [])),
    tag = "admin"
)]
pub async fn list_invites(
    admin: AuthenticatedUser,
    State(state): State<AppState>,
    Query(query): Query<ListInvitesQuery>,
) -> Result<Json<serde_json::Value>, AppError> {
    ensure_admin(&state.db, admin.user_id).await?;

    let rows = match query.status.as_deref() {
        Some("unused") => {
            sqlx::query_as::<_, InviteRow>(
                "SELECT id, token, email, created_at, expires_at, used_at, used_by \
                 FROM invite_tokens WHERE used_at IS NULL AND expires_at > NOW() ORDER BY created_at DESC",
            )
            .fetch_all(&state.db)
            .await
            .map_err(AppError::Database)?
        }
        Some("used") => {
            sqlx::query_as::<_, InviteRow>(
                "SELECT id, token, email, created_at, expires_at, used_at, used_by \
                 FROM invite_tokens WHERE used_at IS NOT NULL ORDER BY used_at DESC",
            )
            .fetch_all(&state.db)
            .await
            .map_err(AppError::Database)?
        }
        Some("expired") => {
            sqlx::query_as::<_, InviteRow>(
                "SELECT id, token, email, created_at, expires_at, used_at, used_by \
                 FROM invite_tokens WHERE used_at IS NULL AND expires_at <= NOW() ORDER BY expires_at DESC",
            )
            .fetch_all(&state.db)
            .await
            .map_err(AppError::Database)?
        }
        _ => {
            sqlx::query_as::<_, InviteRow>(
                "SELECT id, token, email, created_at, expires_at, used_at, used_by \
                 FROM invite_tokens ORDER BY created_at DESC",
            )
            .fetch_all(&state.db)
            .await
            .map_err(AppError::Database)?
        }
    };

    Ok(Json(serde_json::json!({ "invites": rows })))
}

pub fn admin_router() -> Router<AppState> {
    Router::new()
        .route("/v1/admin/access-requests", get(list_access_requests))
        .route(
            "/v1/admin/access-requests/{id}/approve",
            post(approve_access_request),
        )
        .route(
            "/v1/admin/access-requests/{id}/reject",
            post(reject_access_request),
        )
        .route("/v1/admin/invites", get(list_invites).post(create_invite))
}

// ──────────────────────────────────────────────
// Invite validation (used by auth::register)
// ──────────────────────────────────────────────

/// Validates an invite token and returns (token_id, bound_email) if valid.
/// Caller must mark it as used after successful registration.
pub async fn validate_invite_token(
    pool: &sqlx::PgPool,
    token: &str,
) -> Result<(Uuid, Option<String>), AppError> {
    let row = sqlx::query_as::<
        _,
        (
            Uuid,
            Option<String>,
            chrono::DateTime<Utc>,
            Option<chrono::DateTime<Utc>>,
        ),
    >("SELECT id, email, expires_at, used_at FROM invite_tokens WHERE token = $1")
    .bind(token)
    .fetch_optional(pool)
    .await
    .map_err(AppError::Database)?;

    match row {
        None => Err(AppError::Forbidden {
            message: "Invalid invite token.".to_string(),
            docs_hint: Some("Request access at /request-access".to_string()),
        }),
        Some((_, _, _, Some(_))) => Err(AppError::Forbidden {
            message: "This invite has already been used.".to_string(),
            docs_hint: Some("Request a new invite.".to_string()),
        }),
        Some((_, _, expires_at, None)) if expires_at < Utc::now() => Err(AppError::Forbidden {
            message: "This invite has expired.".to_string(),
            docs_hint: Some("Request a new invite.".to_string()),
        }),
        Some((id, bound_email, _, None)) => Ok((id, bound_email)),
    }
}

/// Marks an invite token as used by the given user.
pub async fn mark_invite_used(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    token_id: Uuid,
    user_id: Uuid,
) -> Result<(), AppError> {
    sqlx::query("UPDATE invite_tokens SET used_at = NOW(), used_by = $1 WHERE id = $2")
        .bind(user_id)
        .bind(token_id)
        .execute(&mut **tx)
        .await
        .map_err(AppError::Database)?;
    Ok(())
}

// ──────────────────────────────────────────────
// Email sending
// ──────────────────────────────────────────────

async fn send_invite_email(
    to_email: &str,
    invite_url: &str,
    expires_at: &chrono::DateTime<Utc>,
) -> bool {
    let api_key = match std::env::var("RESEND_API_KEY") {
        Ok(k) if !k.is_empty() => k,
        _ => {
            tracing::warn!("RESEND_API_KEY not set, skipping invite email to {to_email}");
            return false;
        }
    };

    let from =
        std::env::var("EMAIL_FROM").unwrap_or_else(|_| "Kura <noreply@kura.dev>".to_string());
    let expires_formatted = expires_at.format("%d.%m.%Y").to_string();

    let body = format!(
        "Hallo,\n\n\
         du hast Zugang zu Kura.\n\n\
         Erstelle dein Konto hier: {invite_url}\n\n\
         Der Link ist gueltig bis {expires_formatted}.\n\n\
         Kura ist in aktiver Entwicklung. Als Early-Access-Nutzer hilfst du dabei, \
         das System besser zu machen. Deine Trainingsdaten fliessen anonymisiert \
         in die Verbesserung der Algorithmen ein.\n\n\
         Fragen? Antworte einfach auf diese Email.\n\n\
         -- Kura"
    );

    let client = reqwest::Client::new();
    let result = client
        .post("https://api.resend.com/emails")
        .header("Authorization", format!("Bearer {api_key}"))
        .json(&serde_json::json!({
            "from": from,
            "to": [to_email],
            "subject": "Dein Zugang zu Kura",
            "text": body
        }))
        .send()
        .await;

    match result {
        Ok(resp) if resp.status().is_success() => {
            tracing::info!("Invite email sent to {to_email}");
            true
        }
        Ok(resp) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            tracing::error!("Failed to send invite email to {to_email}: {status} {body}");
            false
        }
        Err(e) => {
            tracing::error!("Failed to send invite email to {to_email}: {e}");
            false
        }
    }
}

// ──────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────

async fn ensure_admin(pool: &sqlx::PgPool, user_id: Uuid) -> Result<(), AppError> {
    let is_admin: bool = sqlx::query_scalar("SELECT is_admin FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_one(pool)
        .await
        .map_err(AppError::Database)?;

    if !is_admin {
        return Err(AppError::Forbidden {
            message: "Admin access required.".to_string(),
            docs_hint: None,
        });
    }
    Ok(())
}
