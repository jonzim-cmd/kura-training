use axum::extract::Path;
use axum::routing::delete;
use axum::{Json, Router};
use serde::Serialize;
use uuid::Uuid;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

#[derive(Serialize, utoipa::ToSchema)]
pub struct AccountDeletedResponse {
    pub message: String,
    pub events_deleted: i64,
    pub projections_deleted: i64,
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
    // Check admin status
    let is_admin: bool = sqlx::query_scalar("SELECT is_admin FROM users WHERE id = $1")
        .bind(admin.user_id)
        .fetch_one(&state.db)
        .await
        .map_err(AppError::Database)?;

    if !is_admin {
        return Err(AppError::Forbidden {
            message: "Admin privileges required".to_string(),
            docs_hint: Some(
                "Only admin users can delete other accounts. \
                 To delete your own account, use DELETE /v1/account."
                    .to_string(),
            ),
        });
    }

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

pub fn self_router() -> Router<AppState> {
    Router::new().route("/v1/account", delete(delete_own_account))
}

pub fn admin_router() -> Router<AppState> {
    Router::new().route("/v1/admin/users/{user_id}", delete(admin_delete_user))
}
