use axum::extract::Path;
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::privacy::get_or_create_analysis_subject_id;
use crate::state::AppState;

#[derive(Serialize, utoipa::ToSchema)]
pub struct AccountDeletedResponse {
    pub message: String,
    pub events_deleted: i64,
    pub projections_deleted: i64,
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

/// DELETE /v1/account — delete your own account and all data
#[utoipa::path(
    delete,
    path = "/v1/account",
    responses(
        (status = 200, description = "Account and all data permanently deleted", body = AccountDeletedResponse),
        (status = 401, description = "Not authenticated"),
    ),
    security(("bearer_auth" = []))
)]
pub async fn delete_own_account(
    user: AuthenticatedUser,
    state: axum::extract::State<AppState>,
) -> Result<Json<AccountDeletedResponse>, AppError> {
    let result = execute_account_deletion(&state.db, user.user_id).await?;
    Ok(Json(result))
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

pub fn self_router() -> Router<AppState> {
    Router::new()
        .route("/v1/account", delete(delete_own_account))
        .route("/v1/account/analysis-subject", get(get_analysis_subject))
}

pub fn admin_router() -> Router<AppState> {
    Router::new()
        .route("/v1/admin/users/{user_id}", delete(admin_delete_user))
        .route(
            "/v1/admin/support/reidentify",
            post(admin_support_reidentify),
        )
}
