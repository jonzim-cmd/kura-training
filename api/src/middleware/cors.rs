use axum::http::{HeaderName, HeaderValue, Method};
use tower_http::cors::CorsLayer;

const CONNECTOR_ORIGINS: &[&str] = &[
    "https://chatgpt.com",
    "https://chat.openai.com",
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

    let origins: Vec<HeaderValue> = origin_values
        .into_iter()
        .filter_map(|origin| origin.parse::<HeaderValue>().ok())
        .collect();

    CorsLayer::new()
        .allow_origin(origins)
        .allow_methods([Method::GET, Method::POST, Method::DELETE, Method::OPTIONS])
        .allow_headers([
            HeaderName::from_static("authorization"),
            HeaderName::from_static("content-type"),
        ])
        .allow_credentials(true)
        .max_age(std::time::Duration::from_secs(3600))
}
