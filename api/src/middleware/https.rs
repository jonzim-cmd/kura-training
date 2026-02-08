use axum::extract::Request;
use axum::http::{HeaderValue, StatusCode, Uri};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};

/// Middleware that enforces HTTPS via the `X-Forwarded-Proto` header.
///
/// When a reverse proxy (Fly.io, nginx, etc.) terminates TLS, it sets
/// `X-Forwarded-Proto: https`. If the header says `http`, we 301-redirect
/// to the HTTPS equivalent. All responses get an HSTS header.
pub async fn require_https(req: Request, next: Next) -> Response {
    let proto = req
        .headers()
        .get("x-forwarded-proto")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("https");

    if proto == "http" {
        let host = req
            .headers()
            .get("host")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("localhost");

        let path_and_query = req
            .uri()
            .path_and_query()
            .map(|pq| pq.as_str())
            .unwrap_or("/");

        let https_uri = format!("https://{host}{path_and_query}");

        if let Ok(uri) = https_uri.parse::<Uri>() {
            let mut response = (StatusCode::MOVED_PERMANENTLY, [("location", uri.to_string())])
                .into_response();
            add_hsts_header(&mut response);
            return response;
        }
    }

    let mut response = next.run(req).await;
    add_hsts_header(&mut response);
    response
}

fn add_hsts_header(response: &mut Response) {
    response.headers_mut().insert(
        "strict-transport-security",
        HeaderValue::from_static("max-age=63072000; includeSubDomains"),
    );
}
