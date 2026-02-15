use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Redirect};
use axum::routing::{get, post};
use axum::{Form, Json, Router};
use chrono::{Duration, Utc};
use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode, decode_header};
use rand::Rng;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use kura_core::auth;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub fn register_router() -> Router<AppState> {
    Router::new().route("/v1/auth/register", post(register))
}

pub fn email_login_router() -> Router<AppState> {
    Router::new().route("/v1/auth/email/login", post(email_login))
}

pub fn me_router() -> Router<AppState> {
    Router::new().route("/v1/auth/me", get(get_me))
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

pub fn device_router() -> Router<AppState> {
    Router::new()
        .route("/v1/auth/device/authorize", post(device_authorize))
        .route("/v1/auth/device/token", post(device_token))
        .route(
            "/v1/auth/device/verify",
            get(device_verify_form).post(device_verify_submit),
        )
}

pub fn oidc_router() -> Router<AppState> {
    Router::new().route("/v1/auth/oidc/{provider}/login", post(oidc_login))
}

const AGENT_ACCESS_TOKEN_TTL_MINUTES: i64 = 30;
const DEVICE_CODE_TTL_MINUTES: i64 = 10;
const DEVICE_CODE_POLL_INTERVAL_SECONDS: i32 = 5;

fn default_agent_token_scopes() -> Vec<String> {
    vec![
        "agent:read".to_string(),
        "agent:write".to_string(),
        "agent:resolve".to_string(),
    ]
}

fn normalize_scopes(scopes: Vec<String>) -> Vec<String> {
    let mut normalized: Vec<String> = scopes
        .into_iter()
        .map(|scope| scope.trim().to_lowercase())
        .filter(|scope| !scope.is_empty())
        .collect();
    normalized.sort();
    normalized.dedup();
    normalized
}

fn normalize_email(email: &str) -> String {
    email.trim().to_lowercase()
}

fn validate_invite_email_binding(
    bound_email: Option<&str>,
    signup_email_normalized: &str,
) -> Result<(), AppError> {
    let Some(bound_email_raw) = bound_email else {
        return Ok(());
    };
    let bound_email_normalized = normalize_email(bound_email_raw);
    if bound_email_normalized.is_empty() {
        return Ok(());
    }
    if bound_email_normalized != signup_email_normalized {
        return Err(AppError::Forbidden {
            message: "This invite is bound to a different email address.".to_string(),
            docs_hint: Some(
                "Register with the invited email address or request a new invite.".to_string(),
            ),
        });
    }
    Ok(())
}

fn normalize_user_code(user_code: &str) -> String {
    user_code
        .trim()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .map(|c| c.to_ascii_uppercase())
        .collect()
}

fn generate_user_code() -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    let mut rng = rand::thread_rng();

    let mut chunk = String::with_capacity(4);
    for _ in 0..4 {
        let idx = rng.gen_range(0..ALPHABET.len());
        chunk.push(ALPHABET[idx] as char);
    }

    let mut second = String::with_capacity(4);
    for _ in 0..4 {
        let idx = rng.gen_range(0..ALPHABET.len());
        second.push(ALPHABET[idx] as char);
    }

    format!("{chunk}-{second}")
}

fn generate_device_code() -> String {
    format!(
        "kura_dc_{}{}",
        Uuid::now_v7().simple(),
        Uuid::now_v7().simple()
    )
}

async fn authenticate_email_password_user_id(
    pool: &sqlx::PgPool,
    email_norm: &str,
    password: &str,
) -> Result<Uuid, AppError> {
    let user = sqlx::query_as::<_, EmailIdentityAuthRow>(
        "SELECT u.id, u.password_hash \
         FROM user_identities ui \
         JOIN users u ON u.id = ui.user_id \
         WHERE ui.provider = 'email_password' \
           AND ui.email_norm = $1 \
           AND u.is_active = TRUE",
    )
    .bind(email_norm)
    .fetch_optional(pool)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "Invalid email or password".to_string(),
        docs_hint: None,
    })?;

    let valid = auth::verify_password(password, &user.password_hash).map_err(AppError::Internal)?;
    if !valid {
        return Err(AppError::Unauthorized {
            message: "Invalid email or password".to_string(),
            docs_hint: None,
        });
    }

    Ok(user.id)
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
    #[serde(default)]
    pub invite_token: Option<String>,
    #[serde(default)]
    pub consent_anonymized_learning: Option<bool>,
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
    use crate::state::SignupGate;

    let email_norm = normalize_email(&req.email);
    if email_norm.is_empty() {
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

    // Invite gate: validate token when SIGNUP_GATE=invite
    let invite_token_id = match state.signup_gate {
        SignupGate::Invite => {
            let token_str = req.invite_token.as_deref().unwrap_or("");
            if token_str.is_empty() {
                return Err(AppError::Forbidden {
                    message: "Registration requires an invite token.".to_string(),
                    docs_hint: Some("Request access at /request-access".to_string()),
                });
            }

            let (token_id, bound_email) =
                super::invite::validate_invite_token(&state.db, token_str).await?;

            validate_invite_email_binding(bound_email.as_deref(), &email_norm)?;

            // Require consent in invite mode
            if req.consent_anonymized_learning != Some(true) {
                return Err(AppError::Validation {
                    message: "Consent to anonymized data usage is required for early access."
                        .to_string(),
                    field: Some("consent_anonymized_learning".to_string()),
                    received: None,
                    docs_hint: Some("Set consent_anonymized_learning: true".to_string()),
                });
            }

            Some(token_id)
        }
        SignupGate::Payment => {
            return Err(AppError::Forbidden {
                message: "Registration requires a payment subscription.".to_string(),
                docs_hint: Some("Payment integration coming soon.".to_string()),
            });
        }
        SignupGate::Open => None,
    };

    let consent = req.consent_anonymized_learning.unwrap_or(false);
    let password_hash = auth::hash_password(&req.password).map_err(|e| AppError::Internal(e))?;

    let user_id = Uuid::now_v7();
    let mut tx = state.db.begin().await?;

    sqlx::query(
        "INSERT INTO users (id, email, password_hash, display_name, consent_anonymized_learning, invited_by_token) \
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(user_id)
    .bind(&email_norm)
    .bind(&password_hash)
    .bind(&req.display_name)
    .bind(consent)
    .bind(invite_token_id)
    .execute(&mut *tx)
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

    // Mark invite token as used
    if let Some(token_id) = invite_token_id {
        super::invite::mark_invite_used(&mut tx, token_id, user_id).await?;
    }

    sqlx::query(
        "INSERT INTO user_identities \
         (user_id, provider, provider_subject, email_norm, email_verified_at) \
         VALUES ($1, 'email_password', $2, $2, NOW())",
    )
    .bind(user_id)
    .bind(&email_norm)
    .execute(&mut *tx)
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

    sqlx::query(
        "INSERT INTO analysis_subjects (user_id, analysis_subject_id) \
         VALUES ($1, 'asub_' || replace(gen_random_uuid()::text, '-', '')) \
         ON CONFLICT (user_id) DO NOTHING",
    )
    .bind(user_id)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    tx.commit().await.map_err(AppError::Database)?;

    Ok((
        StatusCode::CREATED,
        Json(RegisterResponse {
            user_id,
            email: email_norm,
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

fn render_login_form(
    client_id: &str,
    redirect_uri: &str,
    code_challenge: &str,
    state: &str,
) -> String {
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
    let email_norm = normalize_email(&form.email);
    if email_norm.is_empty() {
        return Err(AppError::Validation {
            message: "email must not be empty".to_string(),
            field: Some("email".to_string()),
            received: None,
            docs_hint: None,
        });
    }
    if form.code_challenge.is_empty() {
        return Err(AppError::Validation {
            message: "code_challenge is required".to_string(),
            field: Some("code_challenge".to_string()),
            received: None,
            docs_hint: Some("Generate a PKCE code_challenge using S256.".to_string()),
        });
    }
    validate_oauth_client(&state.db, &form.client_id, &form.redirect_uri).await?;

    let user_id =
        authenticate_email_password_user_id(&state.db, &email_norm, &form.password).await?;

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
    .bind(user_id)
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
// Device Authorization Flow
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct DeviceAuthorizeRequest {
    pub client_id: String,
    #[serde(default)]
    pub scope: Vec<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct DeviceAuthorizeResponse {
    pub device_code: String,
    pub user_code: String,
    pub verification_uri: String,
    pub verification_uri_complete: String,
    pub expires_in: i64,
    pub interval: i32,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct DeviceTokenRequest {
    pub device_code: String,
    pub client_id: String,
}

#[derive(Debug, Deserialize)]
pub struct DeviceVerifyQuery {
    #[serde(default)]
    pub user_code: Option<String>,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct DeviceVerifySubmit {
    pub user_code: String,
    pub email: String,
    pub password: String,
    #[serde(default)]
    pub decision: Option<String>,
}

#[derive(sqlx::FromRow)]
struct DeviceTokenRow {
    id: Uuid,
    client_id: String,
    scopes: Vec<String>,
    status: String,
    approved_user_id: Option<Uuid>,
    interval_seconds: i32,
    poll_count: i32,
    last_polled_at: Option<chrono::DateTime<Utc>>,
    expires_at: chrono::DateTime<Utc>,
}

#[derive(sqlx::FromRow)]
struct DeviceVerifyRow {
    id: Uuid,
    status: String,
    expires_at: chrono::DateTime<Utc>,
}

async fn validate_oauth_client_for_device(
    pool: &sqlx::PgPool,
    client_id: &str,
) -> Result<(), AppError> {
    if client_id.trim().is_empty() {
        return Err(AppError::Validation {
            message: "client_id is required".to_string(),
            field: Some("client_id".to_string()),
            received: None,
            docs_hint: Some("Use a registered OAuth client_id.".to_string()),
        });
    }

    let is_active =
        sqlx::query_scalar::<_, bool>("SELECT is_active FROM oauth_clients WHERE client_id = $1")
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

    if !is_active {
        return Err(AppError::Unauthorized {
            message: format!("OAuth client '{}' is inactive", client_id),
            docs_hint: Some("Use an active OAuth client.".to_string()),
        });
    }

    Ok(())
}

fn device_verification_uri() -> String {
    let base = std::env::var("KURA_PUBLIC_BASE_URL")
        .unwrap_or_else(|_| "http://localhost:3000".to_string());
    format!("{}/v1/auth/device/verify", base.trim_end_matches('/'))
}

fn render_device_verify_form(prefilled_user_code: &str, error_message: Option<&str>) -> String {
    let error_html = error_message
        .map(|msg| format!(r#"<p style="color:#b00020;">{}</p>"#, html_escape(msg)))
        .unwrap_or_default();

    format!(
        r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kura — Device verification</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 60px auto; padding: 0 20px; }}
h1 {{ font-size: 1.4em; }}
label {{ display: block; margin-top: 12px; font-weight: 500; }}
input[type="text"], input[type="email"], input[type="password"] {{ width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box; }}
button {{ margin-top: 20px; padding: 10px 24px; background: #111; color: #fff; border: none; cursor: pointer; font-size: 1em; }}
.actions {{ display: flex; gap: 8px; }}
.button-secondary {{ background: #fff; color: #111; border: 1px solid #111; }}
.info {{ color: #666; font-size: 0.9em; margin-top: 8px; }}
</style>
</head>
<body>
<h1>Authorize device</h1>
<p class="info">Enter the code shown in your CLI/MCP client and sign in.</p>
{error_html}
<form method="POST" action="/v1/auth/device/verify">
<label>User code<input type="text" name="user_code" value="{user_code}" required autofocus></label>
<label>Email<input type="email" name="email" required></label>
<label>Password<input type="password" name="password" required></label>
<div class="actions">
<button type="submit" name="decision" value="approve">Authorize device</button>
<button type="submit" name="decision" value="deny" class="button-secondary">Deny</button>
</div>
</form>
</body>
</html>"#,
        error_html = error_html,
        user_code = html_escape(prefilled_user_code),
    )
}

fn render_device_verify_success() -> String {
    r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kura — Device authorized</title>
</head>
<body style="font-family: system-ui, sans-serif; max-width: 420px; margin: 60px auto; padding: 0 20px;">
<h1>Device authorized</h1>
<p>You can return to your CLI or MCP client. This tab can be closed.</p>
</body>
</html>"#
        .to_string()
}

fn render_device_verify_denied() -> String {
    r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kura — Device denied</title>
</head>
<body style="font-family: system-ui, sans-serif; max-width: 420px; margin: 60px auto; padding: 0 20px;">
<h1>Device denied</h1>
<p>The login request was denied. You can close this tab.</p>
</body>
</html>"#
        .to_string()
}

#[utoipa::path(
    post,
    path = "/v1/auth/device/authorize",
    request_body = DeviceAuthorizeRequest,
    responses(
        (status = 200, description = "Device authorization initiated", body = DeviceAuthorizeResponse),
        (status = 400, description = "Invalid request", body = kura_core::error::ApiError)
    ),
    tag = "auth"
)]
pub async fn device_authorize(
    State(state): State<AppState>,
    Json(req): Json<DeviceAuthorizeRequest>,
) -> Result<Json<DeviceAuthorizeResponse>, AppError> {
    validate_oauth_client_for_device(&state.db, &req.client_id).await?;

    let requested_scopes = normalize_scopes(req.scope);
    let scopes = if requested_scopes.is_empty() {
        default_agent_token_scopes()
    } else {
        requested_scopes
    };

    let device_code = generate_device_code();
    let device_code_hash = auth::hash_token(&device_code);
    let user_code = generate_user_code();
    let user_code_hash = auth::hash_token(&normalize_user_code(&user_code));
    let expires_at = Utc::now() + Duration::minutes(DEVICE_CODE_TTL_MINUTES);

    sqlx::query(
        "INSERT INTO oauth_device_codes \
         (device_code_hash, user_code_hash, client_id, scopes, status, interval_seconds, expires_at) \
         VALUES ($1, $2, $3, $4, 'pending', $5, $6)",
    )
    .bind(&device_code_hash)
    .bind(&user_code_hash)
    .bind(&req.client_id)
    .bind(scopes)
    .bind(DEVICE_CODE_POLL_INTERVAL_SECONDS)
    .bind(expires_at)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    let verification_uri = device_verification_uri();
    let verification_uri_complete = format!("{}?user_code={}", verification_uri, user_code);

    Ok(Json(DeviceAuthorizeResponse {
        device_code,
        user_code,
        verification_uri,
        verification_uri_complete,
        expires_in: DEVICE_CODE_TTL_MINUTES * 60,
        interval: DEVICE_CODE_POLL_INTERVAL_SECONDS,
    }))
}

#[utoipa::path(
    get,
    path = "/v1/auth/device/verify",
    params(("user_code" = Option<String>, Query, description = "Optional prefilled user code")),
    responses((status = 200, description = "Verification form HTML")),
    tag = "auth"
)]
pub async fn device_verify_form(
    Query(query): Query<DeviceVerifyQuery>,
) -> Result<Html<String>, AppError> {
    Ok(Html(render_device_verify_form(
        query.user_code.as_deref().unwrap_or(""),
        None,
    )))
}

#[utoipa::path(
    post,
    path = "/v1/auth/device/verify",
    responses(
        (status = 200, description = "Device approved"),
        (status = 401, description = "Invalid credentials")
    ),
    tag = "auth"
)]
pub async fn device_verify_submit(
    State(state): State<AppState>,
    Form(form): Form<DeviceVerifySubmit>,
) -> Result<Html<String>, AppError> {
    let decision = form
        .decision
        .as_deref()
        .unwrap_or("approve")
        .trim()
        .to_lowercase();
    if decision != "approve" && decision != "deny" {
        return Ok(Html(render_device_verify_form(
            &form.user_code,
            Some("Invalid decision. Use approve or deny."),
        )));
    }
    let target_status = if decision == "deny" {
        "denied"
    } else {
        "approved"
    };

    let user_code_norm = normalize_user_code(&form.user_code);
    if user_code_norm.is_empty() {
        return Ok(Html(render_device_verify_form(
            "",
            Some("User code is required."),
        )));
    }

    let user_code_hash = auth::hash_token(&user_code_norm);
    let row = sqlx::query_as::<_, DeviceVerifyRow>(
        "SELECT id, status, expires_at FROM oauth_device_codes WHERE user_code_hash = $1",
    )
    .bind(&user_code_hash)
    .fetch_optional(&state.db)
    .await
    .map_err(AppError::Database)?;

    let Some(row) = row else {
        return Ok(Html(render_device_verify_form(
            &form.user_code,
            Some("Unknown or invalid user code."),
        )));
    };

    if Utc::now() > row.expires_at {
        let _ = sqlx::query(
            "UPDATE oauth_device_codes SET status = 'expired', updated_at = NOW() \
             WHERE id = $1 AND status = 'pending'",
        )
        .bind(row.id)
        .execute(&state.db)
        .await;

        return Ok(Html(render_device_verify_form(
            &form.user_code,
            Some("This device code has expired. Start login again."),
        )));
    }

    if row.status != "pending" {
        return Ok(Html(render_device_verify_form(
            &form.user_code,
            Some("This device code is no longer pending."),
        )));
    }

    let email_norm = normalize_email(&form.email);
    let user_id =
        match authenticate_email_password_user_id(&state.db, &email_norm, &form.password).await {
            Ok(user_id) => user_id,
            Err(AppError::Unauthorized { .. }) => {
                return Ok(Html(render_device_verify_form(
                    &form.user_code,
                    Some("Invalid email or password."),
                )));
            }
            Err(other) => return Err(other),
        };

    let updated = sqlx::query(
        "UPDATE oauth_device_codes \
         SET status = $2, approved_user_id = $3, approved_at = NOW(), updated_at = NOW() \
         WHERE id = $1 AND status = 'pending'",
    )
    .bind(row.id)
    .bind(target_status)
    .bind(user_id)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    if updated.rows_affected() == 0 {
        return Ok(Html(render_device_verify_form(
            &form.user_code,
            Some("This device code was already processed."),
        )));
    }

    if decision == "deny" {
        return Ok(Html(render_device_verify_denied()));
    }
    Ok(Html(render_device_verify_success()))
}

#[utoipa::path(
    post,
    path = "/v1/auth/device/token",
    request_body = DeviceTokenRequest,
    responses(
        (status = 200, description = "Tokens issued", body = TokenResponse),
        (status = 400, description = "Pending/invalid request", body = kura_core::error::ApiError),
        (status = 401, description = "Invalid/expired grant", body = kura_core::error::ApiError)
    ),
    tag = "auth"
)]
pub async fn device_token(
    State(state): State<AppState>,
    Json(req): Json<DeviceTokenRequest>,
) -> Result<Json<TokenResponse>, AppError> {
    if req.device_code.trim().is_empty() {
        return Err(AppError::Validation {
            message: "device_code is required".to_string(),
            field: Some("device_code".to_string()),
            received: None,
            docs_hint: None,
        });
    }

    validate_oauth_client_for_device(&state.db, &req.client_id).await?;

    let device_code_hash = auth::hash_token(req.device_code.trim());
    let row = sqlx::query_as::<_, DeviceTokenRow>(
        "SELECT id, client_id, scopes, status, approved_user_id, interval_seconds, poll_count, \
                last_polled_at, expires_at \
         FROM oauth_device_codes \
         WHERE device_code_hash = $1 AND client_id = $2",
    )
    .bind(&device_code_hash)
    .bind(&req.client_id)
    .fetch_optional(&state.db)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "invalid_device_code".to_string(),
        docs_hint: Some("Restart device authorization flow.".to_string()),
    })?;

    if Utc::now() > row.expires_at {
        let _ = sqlx::query(
            "UPDATE oauth_device_codes SET status = 'expired', updated_at = NOW() \
             WHERE id = $1 AND status IN ('pending', 'approved')",
        )
        .bind(row.id)
        .execute(&state.db)
        .await;

        return Err(AppError::Validation {
            message: "expired_token".to_string(),
            field: None,
            received: None,
            docs_hint: Some("Restart device authorization flow.".to_string()),
        });
    }

    if row.status == "pending" {
        if let Some(last_polled_at) = row.last_polled_at {
            let min_next = last_polled_at + Duration::seconds(row.interval_seconds as i64);
            if Utc::now() < min_next {
                return Err(AppError::Validation {
                    message: "slow_down".to_string(),
                    field: Some("interval".to_string()),
                    received: Some(serde_json::Value::Number(serde_json::Number::from(
                        row.interval_seconds,
                    ))),
                    docs_hint: Some(
                        "Increase polling interval for device token requests.".to_string(),
                    ),
                });
            }
        }

        let _ = sqlx::query(
            "UPDATE oauth_device_codes \
             SET poll_count = $2, last_polled_at = NOW(), updated_at = NOW() \
             WHERE id = $1",
        )
        .bind(row.id)
        .bind(row.poll_count + 1)
        .execute(&state.db)
        .await;

        return Err(AppError::Validation {
            message: "authorization_pending".to_string(),
            field: None,
            received: None,
            docs_hint: Some("User must complete code verification in browser.".to_string()),
        });
    }

    if row.status == "denied" {
        return Err(AppError::Unauthorized {
            message: "access_denied".to_string(),
            docs_hint: Some("Device authorization was denied by the user.".to_string()),
        });
    }

    if row.status == "consumed" {
        return Err(AppError::Unauthorized {
            message: "invalid_grant".to_string(),
            docs_hint: Some("Device code has already been consumed.".to_string()),
        });
    }

    if row.status != "approved" {
        return Err(AppError::Unauthorized {
            message: "invalid_grant".to_string(),
            docs_hint: Some("Device code is not in an approvable state.".to_string()),
        });
    }

    let user_id = row.approved_user_id.ok_or_else(|| AppError::Unauthorized {
        message: "invalid_grant".to_string(),
        docs_hint: Some("Missing approved user for device grant.".to_string()),
    })?;

    let consumed = sqlx::query(
        "UPDATE oauth_device_codes \
         SET status = 'consumed', updated_at = NOW() \
         WHERE id = $1 AND status = 'approved'",
    )
    .bind(row.id)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    if consumed.rows_affected() == 0 {
        return Err(AppError::Unauthorized {
            message: "invalid_grant".to_string(),
            docs_hint: Some("Device code already consumed.".to_string()),
        });
    }

    let scopes = normalize_scopes(row.scopes);
    let effective_scopes = if scopes.is_empty() {
        default_agent_token_scopes()
    } else {
        scopes
    };

    issue_tokens(&state.db, user_id, &row.client_id, effective_scopes).await
}

// ──────────────────────────────────────────────
// OIDC login/linking (Google + Apple)
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct OidcLoginRequest {
    pub id_token: String,
    #[serde(default)]
    pub client_id: Option<String>,
}

#[derive(Debug, Deserialize)]
struct OidcJwks {
    keys: Vec<OidcJwk>,
}

#[derive(Debug, Deserialize)]
struct OidcJwk {
    kid: Option<String>,
    n: String,
    e: String,
    alg: Option<String>,
    kty: String,
}

#[derive(Debug, Deserialize)]
struct OidcClaims {
    sub: String,
    email: Option<String>,
    #[serde(default)]
    email_verified: Option<serde_json::Value>,
}

struct OidcProviderConfig {
    provider: &'static str,
    issuers: &'static [&'static str],
    jwks_uri: &'static str,
    client_id_env: &'static str,
}

fn oidc_provider_config(provider: &str) -> Result<OidcProviderConfig, AppError> {
    match provider {
        "google" => Ok(OidcProviderConfig {
            provider: "google",
            issuers: &["https://accounts.google.com", "accounts.google.com"],
            jwks_uri: "https://www.googleapis.com/oauth2/v3/certs",
            client_id_env: "KURA_OIDC_GOOGLE_CLIENT_ID",
        }),
        "apple" => Ok(OidcProviderConfig {
            provider: "apple",
            issuers: &["https://appleid.apple.com"],
            jwks_uri: "https://appleid.apple.com/auth/keys",
            client_id_env: "KURA_OIDC_APPLE_CLIENT_ID",
        }),
        _ => Err(AppError::Validation {
            message: "provider must be 'google' or 'apple'".to_string(),
            field: Some("provider".to_string()),
            received: Some(serde_json::Value::String(provider.to_string())),
            docs_hint: Some(
                "Use /v1/auth/oidc/google/login or /v1/auth/oidc/apple/login.".to_string(),
            ),
        }),
    }
}

fn oidc_email_verified(value: &Option<serde_json::Value>) -> bool {
    match value {
        Some(serde_json::Value::Bool(v)) => *v,
        Some(serde_json::Value::String(v)) => v.eq_ignore_ascii_case("true"),
        _ => false,
    }
}

async fn verify_oidc_id_token(
    id_token: &str,
    provider: &OidcProviderConfig,
) -> Result<OidcClaims, AppError> {
    let expected_client_id = std::env::var(provider.client_id_env).map_err(|_| {
        AppError::Internal(format!(
            "{} must be set for OIDC login",
            provider.client_id_env
        ))
    })?;

    let header = decode_header(id_token).map_err(|_| AppError::Unauthorized {
        message: "invalid_oidc_id_token".to_string(),
        docs_hint: Some("ID token header could not be parsed.".to_string()),
    })?;
    let kid = header.kid.ok_or_else(|| AppError::Unauthorized {
        message: "invalid_oidc_id_token".to_string(),
        docs_hint: Some("ID token is missing key id (kid).".to_string()),
    })?;

    let jwks: OidcJwks = reqwest::Client::new()
        .get(provider.jwks_uri)
        .send()
        .await
        .map_err(|_| AppError::Unauthorized {
            message: "oidc_jwks_unavailable".to_string(),
            docs_hint: Some("OIDC provider JWKS endpoint unavailable.".to_string()),
        })?
        .error_for_status()
        .map_err(|_| AppError::Unauthorized {
            message: "oidc_jwks_unavailable".to_string(),
            docs_hint: Some("OIDC provider JWKS returned non-success status.".to_string()),
        })?
        .json()
        .await
        .map_err(|_| AppError::Unauthorized {
            message: "oidc_jwks_invalid".to_string(),
            docs_hint: Some("OIDC provider JWKS response was invalid.".to_string()),
        })?;

    let jwk = jwks
        .keys
        .into_iter()
        .find(|k| {
            k.kid.as_deref() == Some(kid.as_str())
                && k.kty.eq_ignore_ascii_case("rsa")
                && k.alg
                    .as_deref()
                    .map(|a| a.eq_ignore_ascii_case("RS256"))
                    .unwrap_or(true)
        })
        .ok_or_else(|| AppError::Unauthorized {
            message: "oidc_signing_key_not_found".to_string(),
            docs_hint: Some("No matching OIDC signing key for token header kid.".to_string()),
        })?;

    let decoding_key =
        DecodingKey::from_rsa_components(&jwk.n, &jwk.e).map_err(|_| AppError::Unauthorized {
            message: "oidc_signing_key_invalid".to_string(),
            docs_hint: Some("OIDC signing key could not be used for verification.".to_string()),
        })?;

    let mut validation = Validation::new(Algorithm::RS256);
    validation.set_audience(&[expected_client_id]);
    validation.set_issuer(provider.issuers);
    validation.validate_exp = true;

    let token_data = decode::<OidcClaims>(id_token, &decoding_key, &validation).map_err(|_| {
        AppError::Unauthorized {
            message: "invalid_oidc_id_token".to_string(),
            docs_hint: Some("OIDC ID token failed signature or claim validation.".to_string()),
        }
    })?;

    if token_data.claims.sub.trim().is_empty() {
        return Err(AppError::Unauthorized {
            message: "invalid_oidc_subject".to_string(),
            docs_hint: Some("OIDC token did not contain a valid subject claim.".to_string()),
        });
    }

    Ok(token_data.claims)
}

#[utoipa::path(
    post,
    path = "/v1/auth/oidc/{provider}/login",
    params(("provider" = String, Path, description = "OIDC provider: google|apple")),
    request_body = OidcLoginRequest,
    responses(
        (status = 200, description = "OIDC login successful, tokens issued", body = TokenResponse),
        (status = 400, description = "Validation error", body = kura_core::error::ApiError),
        (status = 401, description = "Invalid OIDC token", body = kura_core::error::ApiError)
    ),
    tag = "auth"
)]
pub async fn oidc_login(
    Path(provider): Path<String>,
    State(state): State<AppState>,
    Json(req): Json<OidcLoginRequest>,
) -> Result<Json<TokenResponse>, AppError> {
    if req.id_token.trim().is_empty() {
        return Err(AppError::Validation {
            message: "id_token is required".to_string(),
            field: Some("id_token".to_string()),
            received: None,
            docs_hint: Some("Pass a provider-issued OIDC id_token.".to_string()),
        });
    }

    let provider = provider.trim().to_lowercase();
    let provider_cfg = oidc_provider_config(&provider)?;
    let claims = verify_oidc_id_token(req.id_token.trim(), &provider_cfg).await?;
    let provider_subject = claims.sub.trim().to_string();
    let verified_email_norm = claims
        .email
        .as_deref()
        .map(normalize_email)
        .filter(|email| !email.is_empty())
        .filter(|_| oidc_email_verified(&claims.email_verified));
    let mut tx = state.db.begin().await?;

    let existing_provider_user = sqlx::query_scalar::<_, Uuid>(
        "SELECT user_id FROM user_identities WHERE provider = $1 AND provider_subject = $2",
    )
    .bind(provider_cfg.provider)
    .bind(&provider_subject)
    .fetch_optional(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    let user_id = if let Some(user_id) = existing_provider_user {
        if let Some(email_norm) = verified_email_norm.as_deref() {
            let _ = sqlx::query(
                "UPDATE user_identities \
                 SET email_norm = $3, email_verified_at = NOW(), updated_at = NOW() \
                 WHERE provider = $1 AND provider_subject = $2",
            )
            .bind(provider_cfg.provider)
            .bind(&provider_subject)
            .bind(email_norm)
            .execute(&mut *tx)
            .await;
        }
        user_id
    } else {
        let email_norm = if let Some(email_norm) = verified_email_norm.as_deref() {
            email_norm.to_string()
        } else {
            return Err(AppError::Unauthorized {
                message: "oidc_verified_email_required_for_first_link".to_string(),
                docs_hint: Some(
                    "First-time provider linking requires a verified email claim from the identity provider."
                        .to_string(),
                ),
            });
        };

        let by_identity_email = sqlx::query_scalar::<_, Uuid>(
            "SELECT DISTINCT user_id \
             FROM user_identities \
             WHERE email_norm = $1 AND email_verified_at IS NOT NULL \
             LIMIT 2",
        )
        .bind(&email_norm)
        .fetch_all(&mut *tx)
        .await
        .map_err(AppError::Database)?;

        if by_identity_email.len() > 1 {
            return Err(AppError::Validation {
                message: "ambiguous_email_identity".to_string(),
                field: Some("email".to_string()),
                received: Some(serde_json::Value::String(email_norm)),
                docs_hint: Some(
                    "Multiple accounts map to this email. Manual account linking required."
                        .to_string(),
                ),
            });
        }

        if let Some(existing_user_id) = by_identity_email.first().copied() {
            existing_user_id
        } else if let Some(existing_user_id) =
            sqlx::query_scalar::<_, Uuid>("SELECT id FROM users WHERE email = $1")
                .bind(&email_norm)
                .fetch_optional(&mut *tx)
                .await
                .map_err(AppError::Database)?
        {
            existing_user_id
        } else {
            let new_user_id = Uuid::now_v7();
            let bootstrap_secret = format!("oidc-disabled-password-{}", Uuid::now_v7());
            let password_hash =
                auth::hash_password(&bootstrap_secret).map_err(AppError::Internal)?;

            sqlx::query(
                "INSERT INTO users (id, email, password_hash, display_name) VALUES ($1, $2, $3, NULL)",
            )
            .bind(new_user_id)
            .bind(&email_norm)
            .bind(&password_hash)
            .execute(&mut *tx)
            .await
            .map_err(AppError::Database)?;

            new_user_id
        }
    };

    let is_active = sqlx::query_scalar::<_, bool>("SELECT is_active FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(&mut *tx)
        .await
        .map_err(AppError::Database)?
        .ok_or_else(|| AppError::Unauthorized {
            message: "invalid_grant".to_string(),
            docs_hint: Some("OIDC identity mapped to a missing account.".to_string()),
        })?;
    if !is_active {
        return Err(AppError::Unauthorized {
            message: "account_inactive".to_string(),
            docs_hint: Some("This account is inactive.".to_string()),
        });
    }

    let identity_email = verified_email_norm.as_deref();
    let inserted = sqlx::query(
        "INSERT INTO user_identities \
         (user_id, provider, provider_subject, email_norm, email_verified_at) \
         VALUES ($1, $2, $3, $4, CASE WHEN $4 IS NULL THEN NULL ELSE NOW() END) \
         ON CONFLICT (provider, provider_subject) DO NOTHING",
    )
    .bind(user_id)
    .bind(provider_cfg.provider)
    .bind(&provider_subject)
    .bind(identity_email)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    if inserted.rows_affected() == 0 {
        let existing_user_id = sqlx::query_scalar::<_, Uuid>(
            "SELECT user_id FROM user_identities WHERE provider = $1 AND provider_subject = $2",
        )
        .bind(provider_cfg.provider)
        .bind(&provider_subject)
        .fetch_one(&mut *tx)
        .await
        .map_err(AppError::Database)?;

        if existing_user_id != user_id {
            return Err(AppError::Forbidden {
                message: "identity_already_linked".to_string(),
                docs_hint: Some(
                    "This provider identity is already linked to a different account.".to_string(),
                ),
            });
        }
    }

    sqlx::query(
        "INSERT INTO analysis_subjects (user_id, analysis_subject_id) \
         VALUES ($1, 'asub_' || replace(gen_random_uuid()::text, '-', '')) \
         ON CONFLICT (user_id) DO NOTHING",
    )
    .bind(user_id)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    tx.commit().await.map_err(AppError::Database)?;

    let client_id = req.client_id.unwrap_or_else(|| "kura-web".to_string());
    validate_oauth_client_for_device(&state.db, &client_id).await?;
    issue_tokens(&state.db, user_id, &client_id, default_agent_token_scopes()).await
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
    issue_tokens(
        pool,
        auth_code.user_id,
        client_id,
        default_agent_token_scopes(),
    )
    .await
}

async fn refresh_tokens(
    pool: &sqlx::PgPool,
    refresh_token: &str,
    client_id: &str,
) -> Result<Json<TokenResponse>, AppError> {
    let token_hash = auth::hash_token(refresh_token);

    let rt = sqlx::query_as::<_, RefreshTokenRow>(
        "SELECT id, user_id, access_token_id, client_id, scopes, expires_at \
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
    let scopes = normalize_scopes(rt.scopes);
    let effective_scopes = if scopes.is_empty() {
        default_agent_token_scopes()
    } else {
        scopes
    };
    issue_tokens(pool, rt.user_id, client_id, effective_scopes).await
}

async fn issue_tokens(
    pool: &sqlx::PgPool,
    user_id: Uuid,
    client_id: &str,
    scopes: Vec<String>,
) -> Result<Json<TokenResponse>, AppError> {
    let access_token_id = Uuid::now_v7();
    let (access_token, access_hash) = auth::generate_access_token();
    let access_expires = Utc::now() + Duration::minutes(AGENT_ACCESS_TOKEN_TTL_MINUTES);

    sqlx::query(
        "INSERT INTO oauth_access_tokens (id, user_id, token_hash, client_id, scopes, expires_at) \
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(access_token_id)
    .bind(user_id)
    .bind(&access_hash)
    .bind(client_id)
    .bind(scopes.clone())
    .bind(access_expires)
    .execute(pool)
    .await
    .map_err(AppError::Database)?;

    let refresh_token_id = Uuid::now_v7();
    let (refresh_token, refresh_hash) = auth::generate_refresh_token();
    let refresh_expires = Utc::now() + Duration::days(90);

    sqlx::query(
        "INSERT INTO oauth_refresh_tokens \
         (id, user_id, token_hash, access_token_id, client_id, scopes, expires_at) \
         VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(refresh_token_id)
    .bind(user_id)
    .bind(&refresh_hash)
    .bind(access_token_id)
    .bind(client_id)
    .bind(scopes)
    .bind(refresh_expires)
    .execute(pool)
    .await
    .map_err(AppError::Database)?;

    Ok(Json(TokenResponse {
        access_token,
        refresh_token,
        token_type: "Bearer".to_string(),
        expires_in: AGENT_ACCESS_TOKEN_TTL_MINUTES * 60,
    }))
}

// ──────────────────────────────────────────────
// POST /v1/auth/email/login — SPA-friendly email/password → tokens
// ──────────────────────────────────────────────

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct EmailLoginRequest {
    pub email: String,
    pub password: String,
}

#[utoipa::path(
    post,
    path = "/v1/auth/email/login",
    request_body = EmailLoginRequest,
    responses(
        (status = 200, description = "Login successful, tokens issued", body = TokenResponse),
        (status = 401, description = "Invalid credentials", body = kura_core::error::ApiError)
    ),
    tag = "auth"
)]
pub async fn email_login(
    State(state): State<AppState>,
    Json(req): Json<EmailLoginRequest>,
) -> Result<Json<TokenResponse>, AppError> {
    let email_norm = normalize_email(&req.email);
    if email_norm.is_empty() {
        return Err(AppError::Validation {
            message: "email must not be empty".to_string(),
            field: Some("email".to_string()),
            received: None,
            docs_hint: None,
        });
    }

    let user_id =
        authenticate_email_password_user_id(&state.db, &email_norm, &req.password).await?;

    issue_tokens(&state.db, user_id, "kura-web", default_agent_token_scopes()).await
}

// ──────────────────────────────────────────────
// GET /v1/auth/me — current user info
// ──────────────────────────────────────────────

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct MeResponse {
    pub user_id: Uuid,
    pub email: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    pub is_admin: bool,
    pub created_at: chrono::DateTime<Utc>,
}

#[derive(sqlx::FromRow)]
struct MeRow {
    id: Uuid,
    email: String,
    display_name: Option<String>,
    is_admin: bool,
    created_at: chrono::DateTime<Utc>,
}

#[utoipa::path(
    get,
    path = "/v1/auth/me",
    responses(
        (status = 200, description = "Current user info", body = MeResponse),
        (status = 401, description = "Not authenticated", body = kura_core::error::ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "auth"
)]
pub async fn get_me(
    user: AuthenticatedUser,
    State(state): State<AppState>,
) -> Result<Json<MeResponse>, AppError> {
    let row = sqlx::query_as::<_, MeRow>(
        "SELECT id, email, display_name, is_admin, created_at FROM users WHERE id = $1",
    )
    .bind(user.user_id)
    .fetch_one(&state.db)
    .await
    .map_err(AppError::Database)?;

    Ok(Json(MeResponse {
        user_id: row.id,
        email: row.email,
        display_name: row.display_name,
        is_admin: row.is_admin,
        created_at: row.created_at,
    }))
}

#[derive(sqlx::FromRow)]
struct EmailIdentityAuthRow {
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
    scopes: Vec<String>,
    expires_at: chrono::DateTime<Utc>,
}

#[cfg(test)]
mod tests {
    use super::{
        AppError, generate_user_code, is_valid_loopback_redirect, normalize_email,
        normalize_user_code, oidc_email_verified, validate_invite_email_binding,
        validate_oauth_client,
    };
    use serde_json::json;
    use sqlx::postgres::PgPoolOptions;
    use uuid::Uuid;

    #[test]
    fn loopback_redirect_accepts_valid_localhost_callback() {
        assert!(is_valid_loopback_redirect(
            "http://127.0.0.1:45219/callback"
        ));
        assert!(is_valid_loopback_redirect("http://localhost:3000/callback"));
    }

    #[test]
    fn loopback_redirect_rejects_non_loopback_or_invalid_path() {
        assert!(!is_valid_loopback_redirect(
            "http://example.com:3000/callback"
        ));
        assert!(!is_valid_loopback_redirect(
            "https://127.0.0.1:3000/callback"
        ));
        assert!(!is_valid_loopback_redirect("http://127.0.0.1:3000/wrong"));
    }

    #[test]
    fn normalize_email_trims_and_lowercases() {
        assert_eq!(
            normalize_email("  Alice.Example@Mail.TLD  "),
            "alice.example@mail.tld"
        );
    }

    #[test]
    fn invite_email_binding_accepts_matching_email() {
        let result =
            validate_invite_email_binding(Some("  Alice.Example@Mail.TLD "), "alice.example@mail.tld");
        assert!(result.is_ok());
    }

    #[test]
    fn invite_email_binding_rejects_mismatch() {
        let err = validate_invite_email_binding(Some("invited@example.com"), "other@example.com")
            .expect_err("mismatch must be rejected");
        match err {
            AppError::Forbidden { message, .. } => {
                assert!(message.contains("bound to a different email"));
            }
            other => panic!("unexpected error variant: {other:?}"),
        }
    }

    #[test]
    fn normalize_user_code_removes_separators_and_uppercases() {
        assert_eq!(normalize_user_code("ab12-cd34"), "AB12CD34");
        assert_eq!(normalize_user_code(" ab12 cd34 "), "AB12CD34");
    }

    #[test]
    fn generate_user_code_uses_expected_shape() {
        let code = generate_user_code();
        assert_eq!(code.len(), 9);
        assert_eq!(code.chars().nth(4), Some('-'));
    }

    #[test]
    fn oidc_email_verified_supports_bool_and_string() {
        assert!(oidc_email_verified(&Some(json!(true))));
        assert!(oidc_email_verified(&Some(json!("true"))));
        assert!(!oidc_email_verified(&Some(json!("false"))));
        assert!(!oidc_email_verified(&None));
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
        let err = validate_oauth_client(&pool, &random_client, "http://127.0.0.1:31337/callback")
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

        let err = validate_oauth_client(&pool, &client_id, "http://127.0.0.1:3000/callback")
            .await
            .expect_err("inactive client must fail");

        match err {
            AppError::Unauthorized { .. } => {}
            other => panic!("unexpected error variant: {:?}", other),
        }
    }
}
