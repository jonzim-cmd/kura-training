use axum::http::Response;
use tower_governor::{
    governor::GovernorConfigBuilder, key_extractor::SmartIpKeyExtractor, GovernorError,
    GovernorLayer,
};

type RateLimitLayer = GovernorLayer<SmartIpKeyExtractor, governor::middleware::NoOpMiddleware, axum::body::Body>;

/// Rate limit for POST /v1/auth/register: 5 requests per hour per IP.
pub fn register_layer() -> RateLimitLayer {
    GovernorLayer::new(
        GovernorConfigBuilder::default()
            .per_second(720)
            .burst_size(5)
            .key_extractor(SmartIpKeyExtractor)
            .finish()
            .expect("invalid governor config for register"),
    )
    .error_handler(json_error_handler)
}

/// Rate limit for /v1/auth/authorize: 10 requests per minute per IP.
pub fn authorize_layer() -> RateLimitLayer {
    GovernorLayer::new(
        GovernorConfigBuilder::default()
            .per_second(6)
            .burst_size(10)
            .key_extractor(SmartIpKeyExtractor)
            .finish()
            .expect("invalid governor config for authorize"),
    )
    .error_handler(json_error_handler)
}

/// Rate limit for POST /v1/auth/token: 30 requests per minute per IP.
pub fn token_layer() -> RateLimitLayer {
    GovernorLayer::new(
        GovernorConfigBuilder::default()
            .per_second(2)
            .burst_size(30)
            .key_extractor(SmartIpKeyExtractor)
            .finish()
            .expect("invalid governor config for token"),
    )
    .error_handler(json_error_handler)
}

/// Rate limit for POST /v1/events and /v1/events/batch: 60 requests/minute per IP.
pub fn events_write_layer() -> RateLimitLayer {
    GovernorLayer::new(
        GovernorConfigBuilder::default()
            .per_second(1) // 60 per minute = 1 per second replenish
            .burst_size(20)
            .key_extractor(SmartIpKeyExtractor)
            .finish()
            .expect("invalid governor config for events_write"),
    )
    .error_handler(json_error_handler)
}

/// Rate limit for GET /v1/events: 120 requests/minute per IP.
pub fn events_read_layer() -> RateLimitLayer {
    GovernorLayer::new(
        GovernorConfigBuilder::default()
            .per_millisecond(500) // 120 per minute = 2 per second replenish
            .burst_size(30)
            .key_extractor(SmartIpKeyExtractor)
            .finish()
            .expect("invalid governor config for events_read"),
    )
    .error_handler(json_error_handler)
}

/// Rate limit for GET /v1/projections: 120 requests/minute per IP.
pub fn projections_layer() -> RateLimitLayer {
    GovernorLayer::new(
        GovernorConfigBuilder::default()
            .per_millisecond(500) // 120 per minute = 2 per second replenish
            .burst_size(30)
            .key_extractor(SmartIpKeyExtractor)
            .finish()
            .expect("invalid governor config for projections"),
    )
    .error_handler(json_error_handler)
}

/// Custom error handler that returns JSON in ApiError format with Retry-After header.
fn json_error_handler(err: GovernorError) -> Response<axum::body::Body> {
    let (status, retry_after, message) = match err {
        GovernorError::TooManyRequests { wait_time, .. } => (
            axum::http::StatusCode::TOO_MANY_REQUESTS,
            wait_time.to_string(),
            format!("Too many requests. Retry after {wait_time} seconds."),
        ),
        GovernorError::UnableToExtractKey => (
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            String::new(),
            "Unable to determine client identity for rate limiting".to_string(),
        ),
        GovernorError::Other { code, msg, .. } => (
            code,
            String::new(),
            msg.unwrap_or_default().to_string(),
        ),
    };

    let request_id = uuid::Uuid::now_v7().to_string();
    let body = serde_json::json!({
        "error": kura_core::error::codes::RATE_LIMITED,
        "message": message,
        "request_id": request_id,
    });

    let mut response = Response::builder()
        .status(status)
        .header("content-type", "application/json")
        .body(axum::body::Body::from(body.to_string()))
        .unwrap();

    if !retry_after.is_empty() {
        response
            .headers_mut()
            .insert("retry-after", retry_after.parse().unwrap());
    }

    response
}
