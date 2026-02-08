use axum::extract::FromRequestParts;
use axum::http::request::Parts;
use chrono::Utc;
use uuid::Uuid;

use crate::error::AppError;
use crate::state::AppState;

/// Authenticated user extracted from the `Authorization: Bearer <token>` header.
#[derive(Debug, Clone)]
pub struct AuthenticatedUser {
    pub user_id: Uuid,
    pub auth_method: AuthMethod,
}

#[derive(Debug, Clone)]
pub enum AuthMethod {
    ApiKey { key_id: Uuid },
    AccessToken { token_id: Uuid, client_id: String },
}

impl FromRequestParts<AppState> for AuthenticatedUser {
    type Rejection = AppError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &AppState,
    ) -> Result<Self, Self::Rejection> {
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
        "SELECT id, user_id, expires_at FROM api_keys \
         WHERE key_hash = $1 AND is_revoked = FALSE",
    )
    .bind(&token_hash)
    .fetch_optional(pool)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "Invalid API key".to_string(),
        docs_hint: Some(
            "Check that the API key is correct and has not been revoked.".to_string(),
        ),
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
    })
}

async fn authenticate_access_token(
    token: &str,
    pool: &sqlx::PgPool,
) -> Result<AuthenticatedUser, AppError> {
    let token_hash = kura_core::auth::hash_token(token);

    let row = sqlx::query_as::<_, AccessTokenRow>(
        "SELECT id, user_id, client_id, expires_at FROM oauth_access_tokens \
         WHERE token_hash = $1 AND is_revoked = FALSE",
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
    })
}

#[derive(sqlx::FromRow)]
struct ApiKeyRow {
    id: Uuid,
    user_id: Uuid,
    expires_at: Option<chrono::DateTime<Utc>>,
}

#[derive(sqlx::FromRow)]
struct AccessTokenRow {
    id: Uuid,
    user_id: Uuid,
    client_id: String,
    expires_at: chrono::DateTime<Utc>,
}
