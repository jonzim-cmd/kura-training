use std::convert::Infallible;
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Instant;

use axum::extract::Request;
use axum::response::{IntoResponse, Response};
use tower::{Layer, Service, ServiceExt};
use uuid::Uuid;

use crate::auth::AuthenticatedUser;

/// Tower Layer for access pattern logging.
///
/// Captures request metadata and response timing for analytics.
/// Runs after `InjectAuthLayer` — reads user_id from request extensions.
/// Fire-and-forget DB insert (never blocks or fails the response).
#[derive(Clone)]
pub struct AccessLogLayer {
    pool: sqlx::PgPool,
}

impl AccessLogLayer {
    pub fn new(pool: sqlx::PgPool) -> Self {
        Self { pool }
    }
}

impl<S> Layer<S> for AccessLogLayer {
    type Service = AccessLogService<S>;

    fn layer(&self, inner: S) -> Self::Service {
        AccessLogService {
            inner,
            pool: self.pool.clone(),
        }
    }
}

#[derive(Clone)]
pub struct AccessLogService<S> {
    inner: S,
    pool: sqlx::PgPool,
}

impl<S> Service<Request> for AccessLogService<S>
where
    S: Service<Request, Response = Response, Error = Infallible> + Clone + Send + 'static,
    S::Future: Send + 'static,
{
    type Response = Response;
    type Error = Infallible;
    type Future = Pin<Box<dyn Future<Output = Result<Response, Infallible>> + Send>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, req: Request) -> Self::Future {
        let not_ready = self.inner.clone();
        let ready = std::mem::replace(&mut self.inner, not_ready);
        let pool = self.pool.clone();

        Box::pin(async move {
            let path = req.uri().path().to_owned();

            // Only log API endpoints
            if !path.starts_with("/v1/") {
                return Ok(ready.oneshot(req).await.into_response());
            }

            let start = Instant::now();
            let method = req.method().to_string();
            let user_id: Option<Uuid> = req
                .extensions()
                .get::<AuthenticatedUser>()
                .map(|u| u.user_id);

            let response = ready.oneshot(req).await.into_response();

            let status_code = response.status().as_u16() as i16;
            let response_time_ms = start.elapsed().as_millis().min(i32::MAX as u128) as i32;

            let (projection_type, key) = parse_projection_path(&path);

            // Infer batch_size where possible. For /v1/events/batch, the actual count
            // cannot be extracted in middleware without consuming the request body.
            // The path itself identifies batch vs single; exact count derivable from events if needed.
            let batch_size: Option<i16> = if method == "POST" && path == "/v1/events" {
                Some(1)
            } else {
                None
            };

            // Fire-and-forget insert (never blocks the response)
            tokio::spawn(async move {
                if let Err(e) = sqlx::query(
                    "INSERT INTO api_access_log \
                     (user_id, method, path, projection_type, key, status_code, batch_size, response_time_ms) \
                     VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                )
                .bind(user_id)
                .bind(&method)
                .bind(&path)
                .bind(projection_type.as_deref())
                .bind(key.as_deref())
                .bind(status_code)
                .bind(batch_size)
                .bind(response_time_ms)
                .execute(&pool)
                .await
                {
                    tracing::warn!(error = %e, "Failed to insert access log entry");
                }
            });

            Ok(response)
        })
    }
}

/// Parse projection_type and key from `/v1/projections/{type}/{key}`.
fn parse_projection_path(path: &str) -> (Option<String>, Option<String>) {
    let prefix = "/v1/projections/";
    if let Some(rest) = path.strip_prefix(prefix) {
        let parts: Vec<&str> = rest.splitn(2, '/').collect();
        match parts.as_slice() {
            [ptype, key] if !ptype.is_empty() && !key.is_empty() => {
                (Some((*ptype).to_string()), Some((*key).to_string()))
            }
            [ptype] if !ptype.is_empty() => (Some((*ptype).to_string()), None),
            _ => (None, None),
        }
    } else {
        (None, None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_projection_with_type_and_key() {
        assert_eq!(
            parse_projection_path("/v1/projections/exercise_progression/bench_press"),
            (
                Some("exercise_progression".into()),
                Some("bench_press".into())
            )
        );
    }

    #[test]
    fn parse_projection_with_type_only() {
        assert_eq!(
            parse_projection_path("/v1/projections/training_timeline"),
            (Some("training_timeline".into()), None)
        );
    }

    #[test]
    fn parse_projection_user_profile_me() {
        assert_eq!(
            parse_projection_path("/v1/projections/user_profile/me"),
            (Some("user_profile".into()), Some("me".into()))
        );
    }

    #[test]
    fn parse_non_projection_path() {
        assert_eq!(parse_projection_path("/v1/events"), (None, None));
        assert_eq!(parse_projection_path("/v1/events/batch"), (None, None));
        assert_eq!(parse_projection_path("/v1/auth/token"), (None, None));
    }

    #[test]
    fn parse_projection_with_key_containing_slash() {
        assert_eq!(
            parse_projection_path("/v1/projections/custom/some/nested/key"),
            (Some("custom".into()), Some("some/nested/key".into()))
        );
    }
}
