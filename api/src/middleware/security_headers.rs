use axum::extract::Request;
use axum::http::HeaderValue;
use axum::middleware::Next;
use axum::response::Response;

/// Apply a minimal security-header baseline to all responses.
///
/// Keep the CSP intentionally narrow (`frame-ancestors`) so docs/UI routes are
/// not broken by overly restrictive defaults.
pub async fn apply(req: Request, next: Next) -> Response {
    let mut response = next.run(req).await;
    let headers = response.headers_mut();
    headers.insert(
        "x-content-type-options",
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        "referrer-policy",
        HeaderValue::from_static("strict-origin-when-cross-origin"),
    );
    headers.insert("x-frame-options", HeaderValue::from_static("DENY"));
    headers.insert(
        "content-security-policy",
        HeaderValue::from_static("frame-ancestors 'none'"),
    );
    response
}

#[cfg(test)]
mod tests {
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use axum::routing::get;
    use axum::{Router, middleware};
    use tower::ServiceExt;

    async fn ok() -> StatusCode {
        StatusCode::OK
    }

    #[tokio::test]
    async fn apply_adds_security_headers() {
        let app = Router::new()
            .route("/health", get(ok))
            .layer(middleware::from_fn(super::apply));

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/health")
                    .body(Body::empty())
                    .expect("request should build"),
            )
            .await
            .expect("request should succeed");

        let headers = response.headers();
        assert_eq!(
            headers
                .get("x-content-type-options")
                .expect("x-content-type-options header should exist"),
            "nosniff"
        );
        assert_eq!(
            headers
                .get("referrer-policy")
                .expect("referrer-policy header should exist"),
            "strict-origin-when-cross-origin"
        );
        assert_eq!(
            headers
                .get("x-frame-options")
                .expect("x-frame-options header should exist"),
            "DENY"
        );
        assert_eq!(
            headers
                .get("content-security-policy")
                .expect("content-security-policy header should exist"),
            "frame-ancestors 'none'"
        );
    }
}
