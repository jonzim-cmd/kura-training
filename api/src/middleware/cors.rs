use std::collections::HashSet;
use std::sync::Arc;

use axum::http::{HeaderName, HeaderValue, Method, request::Parts as RequestParts};
use tower_http::cors::{AllowOrigin, CorsLayer};

const CONNECTOR_ORIGINS: &[&str] = &[
    "https://chatgpt.com",
    "https://chat.openai.com",
    "https://platform.openai.com",
    "https://claude.ai",
];

/// Build a CORS layer from the `KURA_CORS_ORIGINS` env var.
///
/// - Origins: comma-separated list (default: `http://localhost:3000`)
/// - Methods: GET, POST, OPTIONS
/// - Headers: Authorization, Content-Type
/// - Credentials: allowed
/// - Max age: 3600s
pub fn build_cors_layer() -> CorsLayer {
    let origins_str =
        std::env::var("KURA_CORS_ORIGINS").unwrap_or_else(|_| "http://localhost:3000".to_string());

    let mut origin_values: Vec<String> = origins_str
        .split(',')
        .filter_map(|s| {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                return None;
            }
            Some(trimmed.to_string())
        })
        .collect();

    for connector_origin in CONNECTOR_ORIGINS {
        if !origin_values
            .iter()
            .any(|existing| existing.eq_ignore_ascii_case(connector_origin))
        {
            origin_values.push((*connector_origin).to_string());
        }
    }

    let allowed_origins = Arc::new(
        origin_values
            .iter()
            .map(|origin| origin.to_ascii_lowercase())
            .collect::<HashSet<_>>(),
    );

    tracing::info!(
        event = "cors_allowlist_configured",
        allowed_origins = ?origin_values,
        "Configured CORS allowlist"
    );

    let allow_origin =
        AllowOrigin::predicate(move |origin: &HeaderValue, request_parts: &RequestParts| {
            let origin_value = origin.to_str().unwrap_or_default();
            let allowed = allowed_origins.contains(&origin_value.to_ascii_lowercase());

            if is_oauth_path(request_parts.uri.path()) {
                tracing::info!(
                    event = "mcp_oauth_cors_origin_check",
                    path = %request_parts.uri.path(),
                    method = %request_parts.method,
                    origin = %origin_value,
                    allowed = allowed,
                    "Evaluated CORS origin for MCP OAuth request"
                );
            }

            allowed
        });

    CorsLayer::new()
        .allow_origin(allow_origin)
        .allow_methods([Method::GET, Method::POST, Method::DELETE, Method::OPTIONS])
        .allow_headers([
            HeaderName::from_static("authorization"),
            HeaderName::from_static("content-type"),
        ])
        .allow_credentials(true)
        .max_age(std::time::Duration::from_secs(3600))
}

fn is_oauth_path(path: &str) -> bool {
    path.starts_with("/oauth/")
        || path.starts_with("/mcp/oauth/")
        || path.contains("/.well-known/oauth-")
}
