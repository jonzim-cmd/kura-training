use std::convert::Infallible;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use axum::extract::Request;
use axum::http::{HeaderValue, Response, StatusCode};
use axum::response::IntoResponse;
use chrono::{DateTime, Duration as ChronoDuration, Utc};
use serde_json::json;
use tokio::sync::RwLock;
use tower::{Layer, Service, ServiceExt};
use uuid::Uuid;

use crate::auth::AuthenticatedUser;

const KILL_SWITCH_CACHE_TTL_SECS: i64 = 0;

#[derive(Clone)]
pub struct KillSwitchLayer {
    pool: sqlx::PgPool,
    cache: Arc<RwLock<KillSwitchCache>>,
}

pub fn agent_layer(pool: sqlx::PgPool) -> KillSwitchLayer {
    KillSwitchLayer {
        pool,
        cache: Arc::new(RwLock::new(KillSwitchCache::default())),
    }
}

impl<S> Layer<S> for KillSwitchLayer {
    type Service = KillSwitchService<S>;

    fn layer(&self, inner: S) -> Self::Service {
        KillSwitchService {
            inner,
            pool: self.pool.clone(),
            cache: self.cache.clone(),
        }
    }
}

#[derive(Clone)]
pub struct KillSwitchService<S> {
    inner: S,
    pool: sqlx::PgPool,
    cache: Arc<RwLock<KillSwitchCache>>,
}

impl<S> Service<Request> for KillSwitchService<S>
where
    S: Service<Request, Response = axum::response::Response, Error = Infallible>
        + Clone
        + Send
        + 'static,
    S::Future: Send + 'static,
{
    type Response = axum::response::Response;
    type Error = Infallible;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, Self::Error>> + Send>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, req: Request) -> Self::Future {
        let not_ready = self.inner.clone();
        let ready = std::mem::replace(&mut self.inner, not_ready);
        let pool = self.pool.clone();
        let cache = self.cache.clone();

        Box::pin(async move {
            let path = req.uri().path().to_string();
            if !path.starts_with("/v1/agent/") {
                return Ok(ready.oneshot(req).await.into_response());
            }

            let method = req.method().to_string();
            let target_user_id = req
                .extensions()
                .get::<AuthenticatedUser>()
                .map(|auth| auth.user_id);

            let status = resolve_kill_switch_status(&pool, &cache).await;
            if !status.is_active {
                return Ok(ready.oneshot(req).await.into_response());
            }

            let reason = status
                .reason
                .as_deref()
                .unwrap_or("Security incident response in progress");
            persist_kill_switch_audit_event(
                pool.clone(),
                "blocked_request",
                None,
                target_user_id,
                Some(path.clone()),
                Some(method.clone()),
                Some(reason.to_string()),
                json!({
                    "activated_at": status.activated_at,
                    "activated_by": status.activated_by
                }),
            );

            let request_id = Uuid::now_v7().to_string();
            let body = json!({
                "error": kura_core::error::codes::FORBIDDEN,
                "message": "Agent access temporarily disabled by security kill switch.",
                "field": "security.kill_switch",
                "received": {
                    "reason": reason,
                    "activated_at": status.activated_at,
                    "activated_by": status.activated_by
                },
                "request_id": request_id,
                "docs_hint": "Wait until the incident is resolved or contact an operator."
            });

            let mut response = Response::builder()
                .status(StatusCode::FORBIDDEN)
                .header("content-type", "application/json")
                .body(axum::body::Body::from(body.to_string()))
                .expect("kill-switch response should build");
            response
                .headers_mut()
                .insert("x-kura-kill-switch", HeaderValue::from_static("active"));
            Ok(response)
        })
    }
}

#[derive(Debug, Clone)]
pub struct KillSwitchStatus {
    pub is_active: bool,
    pub reason: Option<String>,
    pub activated_at: Option<DateTime<Utc>>,
    pub activated_by: Option<Uuid>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Default)]
struct KillSwitchCache {
    status: Option<KillSwitchStatus>,
    fetched_at: Option<DateTime<Utc>>,
}

#[derive(sqlx::FromRow)]
struct KillSwitchStatusRow {
    is_active: bool,
    reason: Option<String>,
    activated_at: Option<DateTime<Utc>>,
    activated_by: Option<Uuid>,
    updated_at: DateTime<Utc>,
}

pub async fn fetch_kill_switch_status(
    pool: &sqlx::PgPool,
) -> Result<KillSwitchStatus, sqlx::Error> {
    let row = sqlx::query_as::<_, KillSwitchStatusRow>(
        r#"
        SELECT is_active, reason, activated_at, activated_by, updated_at
        FROM security_kill_switch_state
        WHERE id = TRUE
        "#,
    )
    .fetch_optional(pool)
    .await?;

    Ok(match row {
        Some(row) => KillSwitchStatus {
            is_active: row.is_active,
            reason: row.reason,
            activated_at: row.activated_at,
            activated_by: row.activated_by,
            updated_at: row.updated_at,
        },
        None => KillSwitchStatus {
            is_active: false,
            reason: None,
            activated_at: None,
            activated_by: None,
            updated_at: Utc::now(),
        },
    })
}

async fn resolve_kill_switch_status(
    pool: &sqlx::PgPool,
    cache: &Arc<RwLock<KillSwitchCache>>,
) -> KillSwitchStatus {
    let now = Utc::now();
    {
        let read = cache.read().await;
        if let (Some(status), Some(fetched_at)) = (&read.status, read.fetched_at)
            && now - fetched_at <= ChronoDuration::seconds(KILL_SWITCH_CACHE_TTL_SECS)
        {
            return status.clone();
        }
    }

    match fetch_kill_switch_status(pool).await {
        Ok(status) => {
            let mut write = cache.write().await;
            write.status = Some(status.clone());
            write.fetched_at = Some(now);
            status
        }
        Err(err) => {
            tracing::warn!(error = %err, "kill-switch state lookup failed; defaulting to inactive");
            KillSwitchStatus {
                is_active: false,
                reason: None,
                activated_at: None,
                activated_by: None,
                updated_at: now,
            }
        }
    }
}

pub fn persist_kill_switch_audit_event(
    pool: sqlx::PgPool,
    action: &str,
    actor_user_id: Option<Uuid>,
    target_user_id: Option<Uuid>,
    path: Option<String>,
    method: Option<String>,
    reason: Option<String>,
    metadata: serde_json::Value,
) {
    let action = action.to_string();
    tokio::spawn(async move {
        if let Err(err) = sqlx::query(
            r#"
            INSERT INTO security_kill_switch_audit (
                action,
                actor_user_id,
                target_user_id,
                path,
                method,
                reason,
                metadata
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            "#,
        )
        .bind(action)
        .bind(actor_user_id)
        .bind(target_user_id)
        .bind(path)
        .bind(method)
        .bind(reason)
        .bind(metadata)
        .execute(&pool)
        .await
        {
            tracing::warn!(error = %err, "failed to persist kill-switch audit event");
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{KillSwitchStatus, KillSwitchStatusRow};
    use chrono::Utc;
    use uuid::Uuid;

    #[test]
    fn kill_switch_status_row_maps_expected_values() {
        let now = Utc::now();
        let actor = Uuid::now_v7();
        let row = KillSwitchStatusRow {
            is_active: true,
            reason: Some("incident".to_string()),
            activated_at: Some(now),
            activated_by: Some(actor),
            updated_at: now,
        };
        let status = KillSwitchStatus {
            is_active: row.is_active,
            reason: row.reason.clone(),
            activated_at: row.activated_at,
            activated_by: row.activated_by,
            updated_at: row.updated_at,
        };
        assert!(status.is_active);
        assert_eq!(status.reason.as_deref(), Some("incident"));
        assert_eq!(status.activated_by, Some(actor));
    }
}
