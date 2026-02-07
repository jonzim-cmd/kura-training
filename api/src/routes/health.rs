use axum::{Router, Json, routing::get};

use crate::state::AppState;
use crate::HealthResponse;

pub fn router() -> Router<AppState> {
    Router::new().route("/health", get(health_check))
}

/// Health check endpoint
#[utoipa::path(
    get,
    path = "/health",
    responses(
        (status = 200, description = "Service is healthy", body = HealthResponse)
    ),
    tag = "system"
)]
pub async fn health_check() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok".to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
    })
}
