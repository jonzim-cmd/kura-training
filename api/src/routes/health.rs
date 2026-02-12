use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::{Json, Router, routing::get};

use crate::HealthResponse;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new().route("/health", get(health_check))
}

/// Health check endpoint — verifies both API and database are operational
#[utoipa::path(
    get,
    path = "/health",
    responses(
        (status = 200, description = "Service is healthy", body = HealthResponse),
        (status = 503, description = "Service is unhealthy", body = HealthResponse)
    ),
    tag = "system"
)]
pub async fn health_check(State(state): State<AppState>) -> impl IntoResponse {
    let db_ok = sqlx::query_scalar::<_, i32>("SELECT 1")
        .fetch_one(&state.db)
        .await
        .is_ok();

    let status = if db_ok { "ok" } else { "degraded" };
    let http_status = if db_ok {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };

    (
        http_status,
        Json(HealthResponse {
            status: status.to_string(),
            version: env!("CARGO_PKG_VERSION").to_string(),
        }),
    )
}
