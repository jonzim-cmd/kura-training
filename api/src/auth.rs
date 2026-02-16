use std::convert::Infallible;
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};

use axum::extract::{FromRequestParts, Request};
use axum::http::request::Parts;
use axum::response::{IntoResponse, Response};
use chrono::Utc;
use tower::{Layer, Service, ServiceExt};
use uuid::Uuid;

use crate::error::AppError;
use crate::state::AppState;

/// Authenticated user extracted from the `Authorization: Bearer <token>` header.
///
/// Two-phase resolution:
/// 1. Auth middleware (`InjectAuthLayer`) runs first — validates token, injects into extensions
/// 2. Handler extractor reads from extensions (no DB hit) — or falls back to full auth
#[derive(Debug, Clone)]
pub struct AuthenticatedUser {
    pub user_id: Uuid,
    pub auth_method: AuthMethod,
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone)]
pub enum AuthMethod {
    ApiKey { key_id: Uuid },
    AccessToken { token_id: Uuid, client_id: String },
}

fn scope_matches(granted: &str, required: &str) -> bool {
    let granted = granted.trim().to_lowercase();
    let required = required.trim().to_lowercase();
    if granted.is_empty() || required.is_empty() {
        return false;
    }
    if granted == "*" || granted == required {
        return true;
    }
    if let Some(prefix) = granted.strip_suffix(":*") {
        return required == prefix || required.starts_with(&format!("{prefix}:"));
    }
    false
}

fn has_any_required_scope(granted_scopes: &[String], required_scopes: &[&str]) -> bool {
    if required_scopes.is_empty() {
        return true;
    }
    if granted_scopes.is_empty() {
        return false;
    }
    required_scopes.iter().any(|required| {
        granted_scopes
            .iter()
            .any(|granted| scope_matches(granted, required))
    })
}

pub fn require_scopes(
    auth: &AuthenticatedUser,
    required_scopes: &[&str],
    operation: &str,
) -> Result<(), AppError> {
    if has_any_required_scope(&auth.scopes, required_scopes) {
        tracing::info!(
            user_id = %auth.user_id,
            operation = operation,
            required_scopes = ?required_scopes,
            granted_scopes = ?auth.scopes,
            decision = "allow",
            "scope authorization decision"
        );
        return Ok(());
    }

    let required: Vec<String> = required_scopes
        .iter()
        .map(|scope| scope.to_string())
        .collect();
    let granted = if auth.scopes.is_empty() {
        "none".to_string()
    } else {
        auth.scopes.join(", ")
    };
    tracing::warn!(
        user_id = %auth.user_id,
        operation = operation,
        required_scopes = ?required_scopes,
        granted_scopes = ?auth.scopes,
        decision = "deny",
        "scope authorization decision"
    );

    Err(AppError::Forbidden {
        message: format!("Insufficient scope for operation '{operation}'"),
        docs_hint: Some(format!(
            "Required one of: {}. Granted: {}. Issue a short-lived token/API key with matching scope claims.",
            required.join(", "),
            granted
        )),
    })
}

// --- Tower Layer/Service for auth injection ---

/// Tower Layer that injects `AuthenticatedUser` into request extensions.
/// Silently continues on auth failure (unauthenticated endpoints like health, auth).
#[derive(Clone)]
pub struct InjectAuthLayer {
    pool: sqlx::PgPool,
}

impl InjectAuthLayer {
    pub fn new(pool: sqlx::PgPool) -> Self {
        Self { pool }
    }
}

impl<S> Layer<S> for InjectAuthLayer {
    type Service = InjectAuthService<S>;

    fn layer(&self, inner: S) -> Self::Service {
        InjectAuthService {
            inner,
            pool: self.pool.clone(),
        }
    }
}

#[derive(Clone)]
pub struct InjectAuthService<S> {
    inner: S,
    pool: sqlx::PgPool,
}

impl<S> Service<Request> for InjectAuthService<S>
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

    fn call(&mut self, mut req: Request) -> Self::Future {
        let not_ready = self.inner.clone();
        let ready = std::mem::replace(&mut self.inner, not_ready);
        let pool = self.pool.clone();

        // Extract token synchronously (headers are Send-safe, Body is not)
        let token = extract_bearer_token(&req);

        Box::pin(async move {
            if let Some(token) = token {
                if let Some(auth_user) = authenticate_token(&token, &pool).await {
                    req.extensions_mut().insert(auth_user);
                }
            }
            Ok(ready.oneshot(req).await.into_response())
        })
    }
}

/// Extract bearer token from Authorization header (synchronous, no body access).
fn extract_bearer_token(req: &Request) -> Option<String> {
    let auth_header = req.headers().get("authorization")?.to_str().ok()?;
    auth_header.strip_prefix("Bearer ").map(|s| s.to_owned())
}

/// Authenticate a bearer token. Returns None on any failure.
async fn authenticate_token(token: &str, pool: &sqlx::PgPool) -> Option<AuthenticatedUser> {
    if token.starts_with("kura_sk_") {
        authenticate_api_key(token, pool).await.ok()
    } else if token.starts_with("kura_at_") {
        authenticate_access_token(token, pool).await.ok()
    } else {
        None
    }
}

// --- Extractor (used by handlers) ---

impl FromRequestParts<AppState> for AuthenticatedUser {
    type Rejection = AppError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &AppState,
    ) -> Result<Self, Self::Rejection> {
        // Fast path: auth middleware already validated the token
        if let Some(user) = parts.extensions.get::<AuthenticatedUser>() {
            return Ok(user.clone());
        }

        // Slow path: no middleware ran (shouldn't happen in normal flow)
        let auth_header = parts
            .headers
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .ok_or_else(|| AppError::Unauthorized {
                message: "Missing Authorization header".to_string(),
                docs_hint: Some(
                    "Include 'Authorization: Bearer <token>' header. \
                     Use an API key (kura_sk_...) or access token (kura_at_...)."
                        .to_string(),
                ),
            })?;

        let token = auth_header
            .strip_prefix("Bearer ")
            .ok_or_else(|| AppError::Unauthorized {
                message: "Authorization header must use Bearer scheme".to_string(),
                docs_hint: Some("Format: 'Authorization: Bearer <token>'".to_string()),
            })?;

        if token.starts_with("kura_sk_") {
            authenticate_api_key(token, &state.db).await
        } else if token.starts_with("kura_at_") {
            authenticate_access_token(token, &state.db).await
        } else {
            Err(AppError::Unauthorized {
                message: "Invalid token format".to_string(),
                docs_hint: Some(
                    "Token must start with 'kura_sk_' (API key) or 'kura_at_' (access token)."
                        .to_string(),
                ),
            })
        }
    }
}

async fn authenticate_api_key(
    token: &str,
    pool: &sqlx::PgPool,
) -> Result<AuthenticatedUser, AppError> {
    let token_hash = kura_core::auth::hash_token(token);

    let row = sqlx::query_as::<_, ApiKeyRow>(
        "SELECT ak.id, ak.user_id, ak.scopes, ak.expires_at \
         FROM api_keys ak \
         JOIN users u ON u.id = ak.user_id \
         WHERE ak.key_hash = $1 \
           AND ak.is_revoked = FALSE \
           AND u.is_active = TRUE",
    )
    .bind(&token_hash)
    .fetch_optional(pool)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "Invalid API key".to_string(),
        docs_hint: Some("Check that the API key is correct and has not been revoked.".to_string()),
    })?;

    if let Some(expires_at) = row.expires_at {
        if Utc::now() > expires_at {
            return Err(AppError::Unauthorized {
                message: "API key has expired".to_string(),
                docs_hint: Some("Create a new API key with 'kura admin create-key'.".to_string()),
            });
        }
    }

    // Fire-and-forget last_used_at update
    let pool_clone = pool.clone();
    let key_id = row.id;
    tokio::spawn(async move {
        let _ = sqlx::query("UPDATE api_keys SET last_used_at = NOW() WHERE id = $1")
            .bind(key_id)
            .execute(&pool_clone)
            .await;
    });

    Ok(AuthenticatedUser {
        user_id: row.user_id,
        auth_method: AuthMethod::ApiKey { key_id: row.id },
        scopes: row.scopes,
    })
}

async fn authenticate_access_token(
    token: &str,
    pool: &sqlx::PgPool,
) -> Result<AuthenticatedUser, AppError> {
    let token_hash = kura_core::auth::hash_token(token);

    let row = sqlx::query_as::<_, AccessTokenRow>(
        "SELECT oat.id, oat.user_id, oat.client_id, oat.scopes, oat.expires_at \
         FROM oauth_access_tokens oat \
         JOIN users u ON u.id = oat.user_id \
         WHERE oat.token_hash = $1 \
           AND oat.is_revoked = FALSE \
           AND u.is_active = TRUE",
    )
    .bind(&token_hash)
    .fetch_optional(pool)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "Invalid access token".to_string(),
        docs_hint: Some(
            "Check that the access token is correct and has not been revoked. \
             If expired, use your refresh token to get a new one."
                .to_string(),
        ),
    })?;

    if Utc::now() > row.expires_at {
        return Err(AppError::Unauthorized {
            message: "Access token has expired".to_string(),
            docs_hint: Some(
                "Use your refresh token to obtain a new access token via POST /v1/auth/token."
                    .to_string(),
            ),
        });
    }

    Ok(AuthenticatedUser {
        user_id: row.user_id,
        auth_method: AuthMethod::AccessToken {
            token_id: row.id,
            client_id: row.client_id,
        },
        scopes: row.scopes,
    })
}

#[derive(sqlx::FromRow)]
struct ApiKeyRow {
    id: Uuid,
    user_id: Uuid,
    scopes: Vec<String>,
    expires_at: Option<chrono::DateTime<Utc>>,
}

#[derive(sqlx::FromRow)]
struct AccessTokenRow {
    id: Uuid,
    user_id: Uuid,
    client_id: String,
    scopes: Vec<String>,
    expires_at: chrono::DateTime<Utc>,
}

#[cfg(test)]
mod tests {
    use super::{
        AuthMethod, AuthenticatedUser, has_any_required_scope, require_scopes, scope_matches,
    };
    use uuid::Uuid;

    #[test]
    fn scope_matching_supports_exact_and_wildcards() {
        assert!(scope_matches("agent:read", "agent:read"));
        assert!(scope_matches("agent:*", "agent:write"));
        assert!(scope_matches("*", "agent:write_with_proof"));
        assert!(!scope_matches("agent:read", "agent:write"));
        assert!(!scope_matches("", "agent:read"));
    }

    #[test]
    fn has_any_required_scope_fails_closed_when_scopes_missing() {
        let granted: Vec<String> = Vec::new();
        assert!(!has_any_required_scope(&granted, &["agent:read"]));
    }

    #[test]
    fn require_scopes_returns_forbidden_when_scope_missing() {
        let auth = AuthenticatedUser {
            user_id: Uuid::now_v7(),
            auth_method: AuthMethod::ApiKey {
                key_id: Uuid::now_v7(),
            },
            scopes: vec!["agent:read".to_string()],
        };

        let err = require_scopes(&auth, &["agent:write"], "POST /v1/agent/write-with-proof");
        assert!(err.is_err());
    }
}
