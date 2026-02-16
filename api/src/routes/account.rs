use axum::extract::Path;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use kura_core::auth;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::privacy::get_or_create_analysis_subject_id;
use crate::state::AppState;

const ACCOUNT_DELETION_GRACE_DAYS: i64 = 30;

#[derive(Serialize, utoipa::ToSchema)]
pub struct AccountDeletedResponse {
    pub message: String,
    pub events_deleted: i64,
    pub projections_deleted: i64,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct DeleteOwnAccountRequest {
    pub password: String,
    #[serde(default)]
    pub confirm_email: Option<String>,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct AccountDeletionScheduledResponse {
    pub message: String,
    pub deletion_scheduled_for: DateTime<Utc>,
    pub grace_period_days: i64,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct AnalysisSubjectResponse {
    pub user_id: Uuid,
    pub analysis_subject_id: String,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct SupportReidentifyRequest {
    pub user_id: Uuid,
    pub reason: String,
    pub ticket_id: String,
    #[serde(default)]
    pub requested_mode: Option<String>,
    #[serde(default)]
    pub expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct SupportReidentifyResponse {
    pub user_id: Uuid,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub email: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    pub analysis_subject_id: String,
    pub audited_at: DateTime<Utc>,
}

/// DELETE /v1/account — schedule deletion of your own account (30-day grace)
#[utoipa::path(
    delete,
    path = "/v1/account",
    request_body = DeleteOwnAccountRequest,
    responses(
        (status = 200, description = "Account deletion scheduled (30-day grace)", body = AccountDeletionScheduledResponse),
        (status = 400, description = "Validation error", body = kura_core::error::ApiError),
        (status = 401, description = "Not authenticated"),
    ),
    security(("bearer_auth" = []))
)]
pub async fn delete_own_account(
    user: AuthenticatedUser,
    state: axum::extract::State<AppState>,
    Json(req): Json<DeleteOwnAccountRequest>,
) -> Result<Json<AccountDeletionScheduledResponse>, AppError> {
    if req.password.is_empty() {
        return Err(AppError::Validation {
            message: "password must not be empty".to_string(),
            field: Some("password".to_string()),
            received: None,
            docs_hint: None,
        });
    }

    let mut tx = state.db.begin().await.map_err(AppError::Database)?;
    let row = sqlx::query_as::<_, AccountCredentialRow>(
        "SELECT email, password_hash, is_active, deletion_scheduled_for \
         FROM users \
         WHERE id = $1 \
         FOR UPDATE",
    )
    .bind(user.user_id)
    .fetch_optional(&mut *tx)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "Account not found".to_string(),
        docs_hint: None,
    })?;

    if let Some(confirm_email) = req.confirm_email.as_deref() {
        let expected = normalize_email(&row.email);
        let provided = normalize_email(confirm_email);
        if !provided.is_empty() && provided != expected {
            return Err(AppError::Validation {
                message: "confirm_email does not match your account email".to_string(),
                field: Some("confirm_email".to_string()),
                received: None,
                docs_hint: Some("Provide your account email exactly.".to_string()),
            });
        }
    }

    let valid =
        auth::verify_password(&req.password, &row.password_hash).map_err(AppError::Internal)?;
    if !valid {
        return Err(AppError::Unauthorized {
            message: "Invalid password".to_string(),
            docs_hint: None,
        });
    }

    if !row.is_active {
        if let Some(scheduled_for) = row.deletion_scheduled_for {
            return Ok(Json(AccountDeletionScheduledResponse {
                message: "Account deletion is already scheduled.".to_string(),
                deletion_scheduled_for: scheduled_for,
                grace_period_days: ACCOUNT_DELETION_GRACE_DAYS,
            }));
        }
        return Err(AppError::Forbidden {
            message: "Account is inactive and cannot be scheduled again.".to_string(),
            docs_hint: Some("Contact support for manual review.".to_string()),
        });
    }

    let requested_at = Utc::now();
    let scheduled_for = requested_at + Duration::days(ACCOUNT_DELETION_GRACE_DAYS);
    sqlx::query(
        "UPDATE users \
         SET is_active = FALSE, \
             deletion_requested_at = $2, \
             deletion_scheduled_for = $3, \
             updated_at = NOW() \
         WHERE id = $1",
    )
    .bind(user.user_id)
    .bind(requested_at)
    .bind(scheduled_for)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    sqlx::query("UPDATE oauth_access_tokens SET is_revoked = TRUE WHERE user_id = $1")
        .bind(user.user_id)
        .execute(&mut *tx)
        .await
        .map_err(AppError::Database)?;
    sqlx::query("UPDATE oauth_refresh_tokens SET is_revoked = TRUE WHERE user_id = $1")
        .bind(user.user_id)
        .execute(&mut *tx)
        .await
        .map_err(AppError::Database)?;
    sqlx::query(
        "UPDATE oauth_authorization_codes \
         SET used_at = COALESCE(used_at, NOW()) \
         WHERE user_id = $1 AND used_at IS NULL",
    )
    .bind(user.user_id)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;
    sqlx::query(
        "UPDATE oauth_device_codes \
         SET status = 'expired', updated_at = NOW() \
         WHERE approved_user_id = $1 AND status IN ('pending', 'approved')",
    )
    .bind(user.user_id)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;
    sqlx::query("UPDATE api_keys SET is_revoked = TRUE WHERE user_id = $1")
        .bind(user.user_id)
        .execute(&mut *tx)
        .await
        .map_err(AppError::Database)?;

    sqlx::query(
        "INSERT INTO background_jobs (user_id, job_type, payload, scheduled_for, priority, max_retries) \
         VALUES ($1, 'account.hard_delete', $2, $3, 100, 5)",
    )
    .bind(user.user_id)
    .bind(serde_json::json!({
        "user_id": user.user_id,
        "deletion_requested_at": requested_at.to_rfc3339(),
    }))
    .bind(scheduled_for)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    tx.commit().await.map_err(AppError::Database)?;

    tracing::info!(
        user_id = %user.user_id,
        deletion_scheduled_for = %scheduled_for,
        "Account deletion scheduled with grace period"
    );

    Ok(Json(AccountDeletionScheduledResponse {
        message: format!(
            "Account deactivated. Permanent deletion is scheduled in {} days.",
            ACCOUNT_DELETION_GRACE_DAYS
        ),
        deletion_scheduled_for: scheduled_for,
        grace_period_days: ACCOUNT_DELETION_GRACE_DAYS,
    }))
}

/// GET /v1/account/analysis-subject — stable pseudonymous subject id for analytics/debug
#[utoipa::path(
    get,
    path = "/v1/account/analysis-subject",
    responses(
        (status = 200, description = "Pseudonymous analysis subject id", body = AnalysisSubjectResponse),
        (status = 401, description = "Not authenticated"),
    ),
    security(("bearer_auth" = []))
)]
pub async fn get_analysis_subject(
    user: AuthenticatedUser,
    state: axum::extract::State<AppState>,
) -> Result<Json<AnalysisSubjectResponse>, AppError> {
    let analysis_subject_id = get_or_create_analysis_subject_id(&state.db, user.user_id)
        .await
        .map_err(AppError::Database)?;

    Ok(Json(AnalysisSubjectResponse {
        user_id: user.user_id,
        analysis_subject_id,
    }))
}

/// DELETE /v1/admin/users/{user_id} — admin deletes any user account
#[utoipa::path(
    delete,
    path = "/v1/admin/users/{user_id}",
    params(("user_id" = Uuid, Path, description = "User ID to delete")),
    responses(
        (status = 200, description = "Account and all data permanently deleted", body = AccountDeletedResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin"),
        (status = 404, description = "User not found"),
    ),
    security(("bearer_auth" = []))
)]
pub async fn admin_delete_user(
    admin: AuthenticatedUser,
    state: axum::extract::State<AppState>,
    Path(target_user_id): Path<Uuid>,
) -> Result<Json<AccountDeletedResponse>, AppError> {
    ensure_admin(&state.db, admin.user_id).await?;

    // Check target user exists
    let exists: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)")
        .bind(target_user_id)
        .fetch_one(&state.db)
        .await
        .map_err(AppError::Database)?;

    if !exists {
        return Err(AppError::NotFound {
            resource: format!("User {}", target_user_id),
        });
    }

    let result = execute_account_deletion(&state.db, target_user_id).await?;

    tracing::info!(
        admin_user_id = %admin.user_id,
        deleted_user_id = %target_user_id,
        events_deleted = result.events_deleted,
        "Admin deleted user account"
    );

    Ok(Json(result))
}

#[derive(sqlx::FromRow)]
struct SupportIdentityRow {
    id: Uuid,
    display_name: Option<String>,
    email: Option<String>,
}

/// POST /v1/admin/support/reidentify — audited break-glass user_id -> identity lookup
#[utoipa::path(
    post,
    path = "/v1/admin/support/reidentify",
    request_body = SupportReidentifyRequest,
    responses(
        (status = 200, description = "Identity lookup result", body = SupportReidentifyResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin"),
        (status = 404, description = "User not found"),
    ),
    security(("bearer_auth" = []))
)]
pub async fn admin_support_reidentify(
    admin: AuthenticatedUser,
    state: axum::extract::State<AppState>,
    Json(req): Json<SupportReidentifyRequest>,
) -> Result<Json<SupportReidentifyResponse>, AppError> {
    ensure_admin(&state.db, admin.user_id).await?;

    let reason = req.reason.trim();
    if reason.len() < 8 {
        return Err(AppError::Validation {
            message: "reason must be at least 8 characters".to_string(),
            field: Some("reason".to_string()),
            received: Some(serde_json::Value::String(req.reason)),
            docs_hint: Some(
                "Provide concrete operational reason for break-glass access.".to_string(),
            ),
        });
    }

    let ticket_id = req.ticket_id.trim();
    if ticket_id.is_empty() {
        return Err(AppError::Validation {
            message: "ticket_id must not be empty".to_string(),
            field: Some("ticket_id".to_string()),
            received: None,
            docs_hint: Some("Reference incident/support ticket id.".to_string()),
        });
    }

    let requested_mode = req
        .requested_mode
        .unwrap_or_else(|| "identity_lookup".to_string())
        .trim()
        .to_lowercase();
    if requested_mode != "identity_lookup" && requested_mode != "incident_debug" {
        return Err(AppError::Validation {
            message: "requested_mode must be identity_lookup or incident_debug".to_string(),
            field: Some("requested_mode".to_string()),
            received: Some(serde_json::Value::String(requested_mode)),
            docs_hint: None,
        });
    }

    let row = sqlx::query_as::<_, SupportIdentityRow>(
        "SELECT u.id, u.display_name, \
                (SELECT ui.email_norm \
                 FROM user_identities ui \
                 WHERE ui.user_id = u.id AND ui.email_norm IS NOT NULL \
                 ORDER BY (ui.provider = 'email_password') DESC, ui.created_at ASC \
                 LIMIT 1) AS email \
         FROM users u \
         WHERE u.id = $1",
    )
    .bind(req.user_id)
    .fetch_optional(&state.db)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::NotFound {
        resource: format!("User {}", req.user_id),
    })?;

    let audited_at = Utc::now();
    let actor = format!("admin_user:{}", admin.user_id);
    sqlx::query(
        "INSERT INTO support_access_audit \
         (actor, target_user_id, reason, ticket_id, requested_mode, expires_at, details) \
         VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(actor)
    .bind(req.user_id)
    .bind(reason)
    .bind(ticket_id)
    .bind(&requested_mode)
    .bind(req.expires_at)
    .bind(serde_json::json!({
        "endpoint": "/v1/admin/support/reidentify",
        "admin_user_id": admin.user_id
    }))
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    let analysis_subject_id = get_or_create_analysis_subject_id(&state.db, row.id)
        .await
        .map_err(AppError::Database)?;

    Ok(Json(SupportReidentifyResponse {
        user_id: row.id,
        email: row.email,
        display_name: row.display_name,
        analysis_subject_id,
        audited_at,
    }))
}

async fn execute_account_deletion(
    pool: &sqlx::PgPool,
    user_id: Uuid,
) -> Result<AccountDeletedResponse, AppError> {
    let row: (i64, i64) =
        sqlx::query_as("SELECT events_deleted, projections_deleted FROM delete_user_account($1)")
            .bind(user_id)
            .fetch_one(pool)
            .await
            .map_err(AppError::Database)?;

    tracing::info!(
        user_id = %user_id,
        events_deleted = row.0,
        projections_deleted = row.1,
        "Account permanently deleted (DSGVO Art. 17)"
    );

    Ok(AccountDeletedResponse {
        message: "Account and all associated data permanently deleted.".to_string(),
        events_deleted: row.0,
        projections_deleted: row.1,
    })
}

fn normalize_email(email: &str) -> String {
    email.trim().to_lowercase()
}

#[derive(sqlx::FromRow)]
struct AccountCredentialRow {
    email: String,
    password_hash: String,
    is_active: bool,
    deletion_scheduled_for: Option<DateTime<Utc>>,
}

async fn ensure_admin(pool: &sqlx::PgPool, user_id: Uuid) -> Result<(), AppError> {
    let is_admin: bool = sqlx::query_scalar("SELECT is_admin FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_one(pool)
        .await
        .map_err(AppError::Database)?;

    if !is_admin {
        return Err(AppError::Forbidden {
            message: "Admin privileges required".to_string(),
            docs_hint: Some("Only admin users can perform this operation.".to_string()),
        });
    }
    Ok(())
}

// ──────────────────────────────────────────────
// Self-service API Key Management
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct CreateApiKeyRequest {
    pub label: String,
    #[serde(default = "default_api_key_scopes")]
    pub scopes: Vec<String>,
}

fn default_api_key_scopes() -> Vec<String> {
    vec![
        "agent:read".to_string(),
        "agent:write".to_string(),
        "agent:resolve".to_string(),
    ]
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct CreateApiKeyResponse {
    pub id: Uuid,
    pub label: String,
    pub key: String,
    pub key_prefix: String,
    pub scopes: Vec<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct ApiKeyInfo {
    pub id: Uuid,
    pub label: String,
    pub key_prefix: String,
    pub scopes: Vec<String>,
    pub created_at: DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_used_at: Option<DateTime<Utc>>,
    pub is_revoked: bool,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct ApiKeyListResponse {
    pub keys: Vec<ApiKeyInfo>,
}

#[derive(sqlx::FromRow)]
struct ApiKeyListRow {
    id: Uuid,
    label: String,
    key_prefix: String,
    scopes: Vec<String>,
    created_at: DateTime<Utc>,
    last_used_at: Option<DateTime<Utc>>,
    is_revoked: bool,
}

/// POST /v1/account/api-keys — create an API key for the current user
#[utoipa::path(
    post,
    path = "/v1/account/api-keys",
    request_body = CreateApiKeyRequest,
    responses(
        (status = 201, description = "API key created (key shown once)", body = CreateApiKeyResponse),
        (status = 400, description = "Validation error", body = kura_core::error::ApiError),
        (status = 401, description = "Not authenticated"),
    ),
    security(("bearer_auth" = [])),
    tag = "account"
)]
pub async fn create_api_key(
    user: AuthenticatedUser,
    state: axum::extract::State<AppState>,
    Json(req): Json<CreateApiKeyRequest>,
) -> Result<impl IntoResponse, AppError> {
    let label = req.label.trim().to_string();
    if label.is_empty() || label.len() > 100 {
        return Err(AppError::Validation {
            message: "label must be 1-100 characters".to_string(),
            field: Some("label".to_string()),
            received: Some(serde_json::Value::String(req.label)),
            docs_hint: None,
        });
    }

    let allowed_scopes = ["agent:read", "agent:write", "agent:resolve"];
    for scope in &req.scopes {
        if !allowed_scopes.contains(&scope.as_str()) {
            return Err(AppError::Validation {
                message: format!("Invalid scope: '{}'. Allowed: {:?}", scope, allowed_scopes),
                field: Some("scopes".to_string()),
                received: Some(serde_json::Value::String(scope.clone())),
                docs_hint: None,
            });
        }
    }

    let scopes = if req.scopes.is_empty() {
        default_api_key_scopes()
    } else {
        req.scopes
    };

    let key_id = Uuid::now_v7();
    let (raw_key, key_hash) = auth::generate_api_key();
    let key_prefix = format!("{}...{}", &raw_key[..12], &raw_key[raw_key.len() - 4..]);
    let now = Utc::now();

    sqlx::query(
        "INSERT INTO api_keys (id, user_id, key_hash, key_prefix, label, scopes, created_at) \
         VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(key_id)
    .bind(user.user_id)
    .bind(&key_hash)
    .bind(&key_prefix)
    .bind(&label)
    .bind(&scopes)
    .bind(now)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    Ok((
        StatusCode::CREATED,
        Json(CreateApiKeyResponse {
            id: key_id,
            label,
            key: raw_key,
            key_prefix,
            scopes,
            created_at: now,
        }),
    ))
}

/// GET /v1/account/api-keys — list the current user's API keys
#[utoipa::path(
    get,
    path = "/v1/account/api-keys",
    responses(
        (status = 200, description = "List of API keys", body = ApiKeyListResponse),
        (status = 401, description = "Not authenticated"),
    ),
    security(("bearer_auth" = [])),
    tag = "account"
)]
pub async fn list_api_keys(
    user: AuthenticatedUser,
    state: axum::extract::State<AppState>,
) -> Result<Json<ApiKeyListResponse>, AppError> {
    let rows = sqlx::query_as::<_, ApiKeyListRow>(
        "SELECT id, label, key_prefix, scopes, created_at, last_used_at, is_revoked \
         FROM api_keys WHERE user_id = $1 ORDER BY created_at DESC",
    )
    .bind(user.user_id)
    .fetch_all(&state.db)
    .await
    .map_err(AppError::Database)?;

    let keys = rows
        .into_iter()
        .map(|r| ApiKeyInfo {
            id: r.id,
            label: r.label,
            key_prefix: r.key_prefix,
            scopes: r.scopes,
            created_at: r.created_at,
            last_used_at: r.last_used_at,
            is_revoked: r.is_revoked,
        })
        .collect();

    Ok(Json(ApiKeyListResponse { keys }))
}

/// DELETE /v1/account/api-keys/{key_id} — revoke an API key
#[utoipa::path(
    delete,
    path = "/v1/account/api-keys/{key_id}",
    params(("key_id" = Uuid, Path, description = "API key ID to revoke")),
    responses(
        (status = 200, description = "API key revoked"),
        (status = 401, description = "Not authenticated"),
        (status = 404, description = "API key not found"),
    ),
    security(("bearer_auth" = [])),
    tag = "account"
)]
pub async fn revoke_api_key(
    user: AuthenticatedUser,
    state: axum::extract::State<AppState>,
    Path(key_id): Path<Uuid>,
) -> Result<Json<serde_json::Value>, AppError> {
    let updated = sqlx::query(
        "UPDATE api_keys SET is_revoked = TRUE WHERE id = $1 AND user_id = $2 AND is_revoked = FALSE",
    )
    .bind(key_id)
    .bind(user.user_id)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    if updated.rows_affected() == 0 {
        return Err(AppError::NotFound {
            resource: format!("API key {}", key_id),
        });
    }

    Ok(Json(serde_json::json!({
        "message": "API key revoked",
        "key_id": key_id
    })))
}

pub fn self_router() -> Router<AppState> {
    Router::new()
        .route("/v1/account", delete(delete_own_account))
        .route("/v1/account/analysis-subject", get(get_analysis_subject))
        .route(
            "/v1/account/api-keys",
            get(list_api_keys).post(create_api_key),
        )
        .route("/v1/account/api-keys/{key_id}", delete(revoke_api_key))
}

pub fn admin_router() -> Router<AppState> {
    Router::new()
        .route("/v1/admin/users/{user_id}", delete(admin_delete_user))
        .route(
            "/v1/admin/support/reidentify",
            post(admin_support_reidentify),
        )
}
