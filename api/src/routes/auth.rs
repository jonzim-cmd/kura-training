use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Redirect};
use axum::routing::{get, post};
use axum::{Form, Json, Router};
use chrono::{Duration, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use kura_core::auth;

use crate::error::AppError;
use crate::state::AppState;

pub fn register_router() -> Router<AppState> {
    Router::new().route("/v1/auth/register", post(register))
}

pub fn authorize_router() -> Router<AppState> {
    Router::new().route(
        "/v1/auth/authorize",
        get(authorize_form).post(authorize_submit),
    )
}

pub fn token_router() -> Router<AppState> {
    Router::new().route("/v1/auth/token", post(token))
}

// ──────────────────────────────────────────────
// POST /v1/auth/register
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct RegisterRequest {
    pub email: String,
    pub password: String,
    #[serde(default)]
    pub display_name: Option<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct RegisterResponse {
    pub user_id: Uuid,
    pub email: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
}

#[utoipa::path(
    post,
    path = "/v1/auth/register",
    request_body = RegisterRequest,
    responses(
        (status = 201, description = "User registered", body = RegisterResponse),
        (status = 400, description = "Validation error", body = kura_core::error::ApiError),
        (status = 409, description = "Email already exists", body = kura_core::error::ApiError)
    ),
    tag = "auth"
)]
pub async fn register(
    State(state): State<AppState>,
    Json(req): Json<RegisterRequest>,
) -> Result<impl IntoResponse, AppError> {
    if req.email.is_empty() {
        return Err(AppError::Validation {
            message: "email must not be empty".to_string(),
            field: Some("email".to_string()),
            received: None,
            docs_hint: None,
        });
    }
    if req.password.len() < 8 {
        return Err(AppError::Validation {
            message: "password must be at least 8 characters".to_string(),
            field: Some("password".to_string()),
            received: None,
            docs_hint: None,
        });
    }

    let password_hash =
        auth::hash_password(&req.password).map_err(|e| AppError::Internal(e))?;

    let user_id = Uuid::now_v7();

    sqlx::query(
        "INSERT INTO users (id, email, password_hash, display_name) VALUES ($1, $2, $3, $4)",
    )
    .bind(user_id)
    .bind(&req.email)
    .bind(&password_hash)
    .bind(&req.display_name)
    .execute(&state.db)
    .await
    .map_err(|e| {
        if let sqlx::Error::Database(ref db_err) = e {
            if db_err.code().as_deref() == Some("23505") {
                return AppError::Validation {
                    message: format!("Email '{}' is already registered", req.email),
                    field: Some("email".to_string()),
                    received: Some(serde_json::Value::String(req.email.clone())),
                    docs_hint: Some("Use a different email address.".to_string()),
                };
            }
        }
        AppError::Database(e)
    })?;

    Ok((
        StatusCode::CREATED,
        Json(RegisterResponse {
            user_id,
            email: req.email,
            display_name: req.display_name,
        }),
    ))
}

// ──────────────────────────────────────────────
// GET /v1/auth/authorize
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct AuthorizeParams {
    pub response_type: String,
    pub client_id: String,
    pub redirect_uri: String,
    pub code_challenge: String,
    pub code_challenge_method: String,
    #[serde(default)]
    pub state: Option<String>,
}

#[utoipa::path(
    get,
    path = "/v1/auth/authorize",
    params(AuthorizeParams),
    responses(
        (status = 200, description = "Login form HTML"),
        (status = 400, description = "Invalid parameters", body = kura_core::error::ApiError)
    ),
    tag = "auth"
)]
pub async fn authorize_form(
    State(state): State<AppState>,
    Query(params): Query<AuthorizeParams>,
) -> Result<Html<String>, AppError> {
    validate_authorize_params(&params)?;
    validate_oauth_client(&state.db, &params.client_id, &params.redirect_uri).await?;

    let html = render_login_form(
        &params.client_id,
        &params.redirect_uri,
        &params.code_challenge,
        &params.state.as_deref().unwrap_or(""),
    );

    Ok(Html(html))
}

fn validate_authorize_params(params: &AuthorizeParams) -> Result<(), AppError> {
    if params.response_type != "code" {
        return Err(AppError::Validation {
            message: "response_type must be 'code'".to_string(),
            field: Some("response_type".to_string()),
            received: Some(serde_json::Value::String(params.response_type.clone())),
            docs_hint: Some("Only Authorization Code flow is supported.".to_string()),
        });
    }
    if params.code_challenge_method != "S256" {
        return Err(AppError::Validation {
            message: "code_challenge_method must be 'S256'".to_string(),
            field: Some("code_challenge_method".to_string()),
            received: Some(serde_json::Value::String(
                params.code_challenge_method.clone(),
            )),
            docs_hint: Some("Only PKCE S256 is supported.".to_string()),
        });
    }
    if params.code_challenge.is_empty() {
        return Err(AppError::Validation {
            message: "code_challenge is required".to_string(),
            field: Some("code_challenge".to_string()),
            received: None,
            docs_hint: Some("Generate a code_challenge using S256(code_verifier).".to_string()),
        });
    }
    Ok(())
}

#[derive(sqlx::FromRow)]
struct OAuthClientRow {
    allowed_redirect_uris: Vec<String>,
    allow_loopback_redirect: bool,
    is_active: bool,
}

fn is_valid_loopback_redirect(redirect_uri: &str) -> bool {
    let Ok(url) = url::Url::parse(redirect_uri) else {
        return false;
    };

    if url.scheme() != "http" {
        return false;
    }

    let Some(host) = url.host_str() else {
        return false;
    };
    if host != "127.0.0.1" && host != "localhost" && host != "::1" {
        return false;
    }

    if url.port().is_none() {
        return false;
    }

    if url.path() != "/callback" {
        return false;
    }

    if url.fragment().is_some() {
        return false;
    }

    true
}

async fn validate_oauth_client(
    pool: &sqlx::PgPool,
    client_id: &str,
    redirect_uri: &str,
) -> Result<(), AppError> {
    if client_id.trim().is_empty() {
        return Err(AppError::Validation {
            message: "client_id is required".to_string(),
            field: Some("client_id".to_string()),
            received: None,
            docs_hint: Some("Register an OAuth client first.".to_string()),
        });
    }

    if redirect_uri.trim().is_empty() {
        return Err(AppError::Validation {
            message: "redirect_uri is required".to_string(),
            field: Some("redirect_uri".to_string()),
            received: None,
            docs_hint: Some("Provide a valid redirect URI for this client.".to_string()),
        });
    }

    let row = sqlx::query_as::<_, OAuthClientRow>(
        "SELECT allowed_redirect_uris, allow_loopback_redirect, is_active \
         FROM oauth_clients WHERE client_id = $1",
    )
    .bind(client_id)
    .fetch_optional(pool)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Validation {
        message: format!("Unknown OAuth client_id '{}'", client_id),
        field: Some("client_id".to_string()),
        received: Some(serde_json::Value::String(client_id.to_string())),
        docs_hint: Some("Use a registered OAuth client_id.".to_string()),
    })?;

    if !row.is_active {
        return Err(AppError::Unauthorized {
            message: format!("OAuth client '{}' is inactive", client_id),
            docs_hint: Some("Use an active OAuth client.".to_string()),
        });
    }

    let exact_match = row
        .allowed_redirect_uris
        .iter()
        .any(|allowed| allowed == redirect_uri);
    if exact_match {
        return Ok(());
    }

    if row.allow_loopback_redirect && is_valid_loopback_redirect(redirect_uri) {
        return Ok(());
    }

    Err(AppError::Validation {
        message: "redirect_uri is not allowed for this client".to_string(),
        field: Some("redirect_uri".to_string()),
        received: Some(serde_json::Value::String(redirect_uri.to_string())),
        docs_hint: Some(
            "Use one of the registered redirect URIs, or a loopback callback \
             if this client allows loopback redirects."
                .to_string(),
        ),
    })
}

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

fn render_login_form(client_id: &str, redirect_uri: &str, code_challenge: &str, state: &str) -> String {
    format!(
        r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kura — Authorize</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 400px; margin: 60px auto; padding: 0 20px; }}
h1 {{ font-size: 1.4em; }}
label {{ display: block; margin-top: 12px; font-weight: 500; }}
input[type="email"], input[type="password"] {{ width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box; }}
button {{ margin-top: 20px; padding: 10px 24px; background: #111; color: #fff; border: none; cursor: pointer; font-size: 1em; }}
.info {{ color: #666; font-size: 0.9em; margin-top: 8px; }}
</style>
</head>
<body>
<h1>Authorize {client_id_escaped}</h1>
<p class="info">Sign in to grant access to your Kura account.</p>
<form method="POST" action="/v1/auth/authorize">
<input type="hidden" name="client_id" value="{client_id_escaped}">
<input type="hidden" name="redirect_uri" value="{redirect_uri_escaped}">
<input type="hidden" name="code_challenge" value="{code_challenge_escaped}">
<input type="hidden" name="state" value="{state_escaped}">
<label>Email<input type="email" name="email" required autofocus></label>
<label>Password<input type="password" name="password" required></label>
<button type="submit">Authorize</button>
</form>
</body>
</html>"#,
        client_id_escaped = html_escape(client_id),
        redirect_uri_escaped = html_escape(redirect_uri),
        code_challenge_escaped = html_escape(code_challenge),
        state_escaped = html_escape(state),
    )
}

// ──────────────────────────────────────────────
// POST /v1/auth/authorize
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct AuthorizeSubmit {
    pub email: String,
    pub password: String,
    pub client_id: String,
    pub redirect_uri: String,
    pub code_challenge: String,
    #[serde(default)]
    pub state: Option<String>,
}

#[utoipa::path(
    post,
    path = "/v1/auth/authorize",
    responses(
        (status = 302, description = "Redirect to client with auth code"),
        (status = 401, description = "Invalid credentials")
    ),
    tag = "auth"
)]
pub async fn authorize_submit(
    State(state): State<AppState>,
    Form(form): Form<AuthorizeSubmit>,
) -> Result<impl IntoResponse, AppError> {
    if form.code_challenge.is_empty() {
        return Err(AppError::Validation {
            message: "code_challenge is required".to_string(),
            field: Some("code_challenge".to_string()),
            received: None,
            docs_hint: Some("Generate a PKCE code_challenge using S256.".to_string()),
        });
    }
    validate_oauth_client(&state.db, &form.client_id, &form.redirect_uri).await?;

    // Verify credentials
    let user = sqlx::query_as::<_, UserRow>(
        "SELECT id, password_hash FROM users WHERE email = $1 AND is_active = TRUE",
    )
    .bind(&form.email)
    .fetch_optional(&state.db)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "Invalid email or password".to_string(),
        docs_hint: None,
    })?;

    let valid = auth::verify_password(&form.password, &user.password_hash)
        .map_err(|e| AppError::Internal(e))?;

    if !valid {
        return Err(AppError::Unauthorized {
            message: "Invalid email or password".to_string(),
            docs_hint: None,
        });
    }

    // Generate auth code (10 min expiry)
    let (code, code_hash) = auth::generate_auth_code();
    let code_id = Uuid::now_v7();
    let expires_at = Utc::now() + Duration::minutes(10);

    sqlx::query(
        "INSERT INTO oauth_authorization_codes \
         (id, user_id, code_hash, client_id, redirect_uri, code_challenge, expires_at) \
         VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(code_id)
    .bind(user.id)
    .bind(&code_hash)
    .bind(&form.client_id)
    .bind(&form.redirect_uri)
    .bind(&form.code_challenge)
    .bind(expires_at)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    // Build redirect URL
    let mut redirect_url =
        url::Url::parse(&form.redirect_uri).map_err(|e| AppError::Validation {
            message: format!("Invalid redirect_uri: {e}"),
            field: Some("redirect_uri".to_string()),
            received: Some(serde_json::Value::String(form.redirect_uri.clone())),
            docs_hint: None,
        })?;

    redirect_url.query_pairs_mut().append_pair("code", &code);
    if let Some(ref s) = form.state {
        redirect_url.query_pairs_mut().append_pair("state", s);
    }

    Ok(Redirect::to(redirect_url.as_str()))
}

// ──────────────────────────────────────────────
// POST /v1/auth/token
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::ToSchema)]
#[serde(tag = "grant_type")]
pub enum TokenRequest {
    #[serde(rename = "authorization_code")]
    AuthorizationCode {
        code: String,
        code_verifier: String,
        redirect_uri: String,
        client_id: String,
    },
    #[serde(rename = "refresh_token")]
    RefreshToken {
        refresh_token: String,
        client_id: String,
    },
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct TokenResponse {
    pub access_token: String,
    pub refresh_token: String,
    pub token_type: String,
    pub expires_in: i64,
}

#[utoipa::path(
    post,
    path = "/v1/auth/token",
    request_body = TokenRequest,
    responses(
        (status = 200, description = "Tokens issued", body = TokenResponse),
        (status = 400, description = "Invalid request", body = kura_core::error::ApiError),
        (status = 401, description = "Invalid grant", body = kura_core::error::ApiError)
    ),
    tag = "auth"
)]
pub async fn token(
    State(state): State<AppState>,
    Json(req): Json<TokenRequest>,
) -> Result<Json<TokenResponse>, AppError> {
    match req {
        TokenRequest::AuthorizationCode {
            code,
            code_verifier,
            redirect_uri,
            client_id,
        } => {
            exchange_authorization_code(&state.db, &code, &code_verifier, &redirect_uri, &client_id)
                .await
        }
        TokenRequest::RefreshToken {
            refresh_token,
            client_id,
        } => refresh_tokens(&state.db, &refresh_token, &client_id).await,
    }
}

async fn exchange_authorization_code(
    pool: &sqlx::PgPool,
    code: &str,
    code_verifier: &str,
    redirect_uri: &str,
    client_id: &str,
) -> Result<Json<TokenResponse>, AppError> {
    let code_hash = auth::hash_token(code);

    let auth_code = sqlx::query_as::<_, AuthCodeRow>(
        "SELECT id, user_id, client_id, redirect_uri, code_challenge, expires_at, used_at \
         FROM oauth_authorization_codes WHERE code_hash = $1",
    )
    .bind(&code_hash)
    .fetch_optional(pool)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "Invalid authorization code".to_string(),
        docs_hint: None,
    })?;

    // Validate: not expired
    if Utc::now() > auth_code.expires_at {
        return Err(AppError::Unauthorized {
            message: "Authorization code has expired".to_string(),
            docs_hint: Some("Restart the authorization flow.".to_string()),
        });
    }

    // Validate: not already used
    if auth_code.used_at.is_some() {
        return Err(AppError::Unauthorized {
            message: "Authorization code has already been used".to_string(),
            docs_hint: Some("Each authorization code can only be used once.".to_string()),
        });
    }

    // Validate: client_id matches
    if auth_code.client_id != client_id {
        return Err(AppError::Unauthorized {
            message: "client_id mismatch".to_string(),
            docs_hint: None,
        });
    }

    // Validate: redirect_uri matches
    if auth_code.redirect_uri != redirect_uri {
        return Err(AppError::Unauthorized {
            message: "redirect_uri mismatch".to_string(),
            docs_hint: None,
        });
    }

    // Validate: PKCE
    if !auth::verify_pkce(code_verifier, &auth_code.code_challenge) {
        return Err(AppError::Unauthorized {
            message: "PKCE verification failed".to_string(),
            docs_hint: Some(
                "Ensure code_verifier matches the code_challenge used during authorization."
                    .to_string(),
            ),
        });
    }

    // Mark code as used
    sqlx::query("UPDATE oauth_authorization_codes SET used_at = NOW() WHERE id = $1")
        .bind(auth_code.id)
        .execute(pool)
        .await
        .map_err(AppError::Database)?;

    // Issue tokens
    issue_tokens(pool, auth_code.user_id, client_id).await
}

async fn refresh_tokens(
    pool: &sqlx::PgPool,
    refresh_token: &str,
    client_id: &str,
) -> Result<Json<TokenResponse>, AppError> {
    let token_hash = auth::hash_token(refresh_token);

    let rt = sqlx::query_as::<_, RefreshTokenRow>(
        "SELECT id, user_id, access_token_id, client_id, expires_at \
         FROM oauth_refresh_tokens WHERE token_hash = $1 AND is_revoked = FALSE",
    )
    .bind(&token_hash)
    .fetch_optional(pool)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "Invalid refresh token".to_string(),
        docs_hint: Some("The refresh token may have been revoked. Re-authenticate.".to_string()),
    })?;

    if Utc::now() > rt.expires_at {
        return Err(AppError::Unauthorized {
            message: "Refresh token has expired".to_string(),
            docs_hint: Some("Re-authenticate to get new tokens.".to_string()),
        });
    }

    if rt.client_id != client_id {
        return Err(AppError::Unauthorized {
            message: "client_id mismatch".to_string(),
            docs_hint: None,
        });
    }

    // Revoke old tokens (rotation)
    sqlx::query("UPDATE oauth_access_tokens SET is_revoked = TRUE WHERE id = $1")
        .bind(rt.access_token_id)
        .execute(pool)
        .await
        .map_err(AppError::Database)?;

    sqlx::query("UPDATE oauth_refresh_tokens SET is_revoked = TRUE WHERE id = $1")
        .bind(rt.id)
        .execute(pool)
        .await
        .map_err(AppError::Database)?;

    // Issue new tokens
    issue_tokens(pool, rt.user_id, client_id).await
}

async fn issue_tokens(
    pool: &sqlx::PgPool,
    user_id: Uuid,
    client_id: &str,
) -> Result<Json<TokenResponse>, AppError> {
    let access_token_id = Uuid::now_v7();
    let (access_token, access_hash) = auth::generate_access_token();
    let access_expires = Utc::now() + Duration::hours(1);

    sqlx::query(
        "INSERT INTO oauth_access_tokens (id, user_id, token_hash, client_id, expires_at) \
         VALUES ($1, $2, $3, $4, $5)",
    )
    .bind(access_token_id)
    .bind(user_id)
    .bind(&access_hash)
    .bind(client_id)
    .bind(access_expires)
    .execute(pool)
    .await
    .map_err(AppError::Database)?;

    let refresh_token_id = Uuid::now_v7();
    let (refresh_token, refresh_hash) = auth::generate_refresh_token();
    let refresh_expires = Utc::now() + Duration::days(90);

    sqlx::query(
        "INSERT INTO oauth_refresh_tokens \
         (id, user_id, token_hash, access_token_id, client_id, expires_at) \
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(refresh_token_id)
    .bind(user_id)
    .bind(&refresh_hash)
    .bind(access_token_id)
    .bind(client_id)
    .bind(refresh_expires)
    .execute(pool)
    .await
    .map_err(AppError::Database)?;

    Ok(Json(TokenResponse {
        access_token,
        refresh_token,
        token_type: "Bearer".to_string(),
        expires_in: 3600,
    }))
}

#[derive(sqlx::FromRow)]
struct UserRow {
    id: Uuid,
    password_hash: String,
}

#[derive(sqlx::FromRow)]
struct AuthCodeRow {
    id: Uuid,
    user_id: Uuid,
    client_id: String,
    redirect_uri: String,
    code_challenge: String,
    expires_at: chrono::DateTime<Utc>,
    used_at: Option<chrono::DateTime<Utc>>,
}

#[derive(sqlx::FromRow)]
struct RefreshTokenRow {
    id: Uuid,
    user_id: Uuid,
    access_token_id: Uuid,
    client_id: String,
    expires_at: chrono::DateTime<Utc>,
}

#[cfg(test)]
mod tests {
    use super::{is_valid_loopback_redirect, validate_oauth_client, AppError};
    use sqlx::postgres::PgPoolOptions;
    use uuid::Uuid;

    #[test]
    fn loopback_redirect_accepts_valid_localhost_callback() {
        assert!(is_valid_loopback_redirect(
            "http://127.0.0.1:45219/callback"
        ));
        assert!(is_valid_loopback_redirect(
            "http://localhost:3000/callback"
        ));
    }

    #[test]
    fn loopback_redirect_rejects_non_loopback_or_invalid_path() {
        assert!(!is_valid_loopback_redirect(
            "http://example.com:3000/callback"
        ));
        assert!(!is_valid_loopback_redirect(
            "https://127.0.0.1:3000/callback"
        ));
        assert!(!is_valid_loopback_redirect(
            "http://127.0.0.1:3000/wrong"
        ));
    }

    async fn db_pool_if_available() -> Option<sqlx::PgPool> {
        let Ok(url) = std::env::var("DATABASE_URL") else {
            return None;
        };

        PgPoolOptions::new()
            .max_connections(1)
            .connect(&url)
            .await
            .ok()
    }

    #[tokio::test]
    async fn validate_oauth_client_unknown_client_is_rejected() {
        let Some(pool) = db_pool_if_available().await else {
            return;
        };

        sqlx::migrate!("../migrations")
            .run(&pool)
            .await
            .expect("migrations should run");

        let random_client = format!("missing-client-{}", Uuid::now_v7());
        let err = validate_oauth_client(
            &pool,
            &random_client,
            "http://127.0.0.1:31337/callback",
        )
        .await
        .expect_err("unknown client must fail");

        match err {
            AppError::Validation { field, .. } => {
                assert_eq!(field.as_deref(), Some("client_id"));
            }
            other => panic!("unexpected error variant: {:?}", other),
        }
    }

    #[tokio::test]
    async fn validate_oauth_client_inactive_client_is_rejected() {
        let Some(pool) = db_pool_if_available().await else {
            return;
        };

        sqlx::migrate!("../migrations")
            .run(&pool)
            .await
            .expect("migrations should run");

        let client_id = format!("inactive-client-{}", Uuid::now_v7());
        sqlx::query(
            "INSERT INTO oauth_clients \
             (client_id, allowed_redirect_uris, allow_loopback_redirect, is_active) \
             VALUES ($1, $2, FALSE, FALSE)",
        )
        .bind(&client_id)
        .bind(vec!["http://127.0.0.1:3000/callback"])
        .execute(&pool)
        .await
        .expect("insert inactive oauth client");

        let err = validate_oauth_client(
            &pool,
            &client_id,
            "http://127.0.0.1:3000/callback",
        )
        .await
        .expect_err("inactive client must fail");

        match err {
            AppError::Unauthorized { .. } => {}
            other => panic!("unexpected error variant: {:?}", other),
        }
    }
}
