use axum::extract::State;
use axum::routing::get;
use axum::{Json, Router};
use serde::Serialize;

use kura_core::error::ApiError;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new().route("/v1/system/config", get(get_system_config))
}

/// Response for GET /v1/system/config
#[derive(Serialize, utoipa::ToSchema)]
pub struct SystemConfigResponse {
    pub data: serde_json::Value,
    pub version: i64,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// Internal row type for sqlx mapping
#[derive(sqlx::FromRow)]
struct SystemConfigRow {
    data: serde_json::Value,
    version: i64,
    updated_at: chrono::DateTime<chrono::Utc>,
}

/// Get deployment-static system configuration
///
/// Returns dimensions, event conventions, interview guide, and normalization
/// conventions. This data is identical for all users and changes only on
/// code deployment. Agents should cache this per session.
#[utoipa::path(
    get,
    path = "/v1/system/config",
    responses(
        (status = 200, description = "System configuration", body = SystemConfigResponse),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 404, description = "System config not yet available (worker has not started)")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_system_config(
    State(state): State<AppState>,
    _auth: AuthenticatedUser,
) -> Result<Json<SystemConfigResponse>, AppError> {
    // No RLS context needed — system_config has no user_id
    let row = sqlx::query_as::<_, SystemConfigRow>(
        "SELECT data, version, updated_at FROM system_config WHERE key = 'global'",
    )
    .fetch_optional(&state.db)
    .await?;

    match row {
        Some(r) => Ok(Json(SystemConfigResponse {
            data: r.data,
            version: r.version,
            updated_at: r.updated_at,
        })),
        None => Err(AppError::NotFound {
            resource: "system_config/global".to_string(),
        }),
    }
}
