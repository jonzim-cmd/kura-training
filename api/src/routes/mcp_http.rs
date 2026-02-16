use std::collections::HashMap;

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::header::{AUTHORIZATION, CONTENT_TYPE, HOST, WWW_AUTHENTICATE};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Form, Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use url::Url;
use uuid::Uuid;

use crate::error::AppError;
use crate::state::AppState;

const MCP_PATH: &str = "/mcp";
const OAUTH_SCOPES: [&str; 3] = ["agent:read", "agent:write", "agent:resolve"];

pub fn router() -> Router<AppState> {
    Router::new()
        .route(MCP_PATH, post(mcp_post).get(mcp_get))
        .route(
            "/.well-known/oauth-authorization-server",
            get(oauth_authorization_server_metadata),
        )
        .route(
            "/.well-known/oauth-authorization-server/mcp",
            get(oauth_authorization_server_metadata),
        )
        .route(
            "/.well-known/oauth-protected-resource",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/.well-known/oauth-protected-resource/mcp",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/oauth/authorize",
            get(oauth_authorize_get).post(oauth_authorize_post),
        )
        .route("/oauth/token", post(oauth_token))
        .route("/oauth/register", post(oauth_register))
}

async fn mcp_get() -> Response {
    StatusCode::METHOD_NOT_ALLOWED.into_response()
}

async fn mcp_post(headers: HeaderMap, body: Bytes) -> Response {
    let public_base_url = request_base_url(&headers);
    let token = match extract_bearer_token(&headers) {
        Ok(token) => token,
        Err(description) => return mcp_oauth_challenge(&public_base_url, description),
    };

    let incoming: Value = match serde_json::from_slice(&body) {
        Ok(payload) => payload,
        Err(_) => {
            return (
                StatusCode::OK,
                Json(json!({
                    "jsonrpc": "2.0",
                    "id": null,
                    "error": {
                        "code": -32700,
                        "message": "Parse error"
                    }
                })),
            )
                .into_response();
        }
    };

    let responses = kura_mcp_runtime::handle_http_jsonrpc(
        &runtime_api_base_url(),
        kura_mcp_runtime::HttpMcpRequestConfig {
            token: Some(token),
            ..Default::default()
        },
        incoming,
    )
    .await;

    if responses.is_empty() {
        return StatusCode::ACCEPTED.into_response();
    }

    if responses.len() == 1 {
        return (
            StatusCode::OK,
            Json(responses.into_iter().next().unwrap_or(Value::Null)),
        )
            .into_response();
    }

    (StatusCode::OK, Json(Value::Array(responses))).into_response()
}

async fn oauth_authorization_server_metadata(headers: HeaderMap) -> Json<Value> {
    let base = request_base_url(&headers);
    Json(json!({
        "issuer": base,
        "authorization_endpoint": format!("{base}/oauth/authorize"),
        "token_endpoint": format!("{base}/oauth/token"),
        "registration_endpoint": format!("{base}/oauth/register"),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": OAUTH_SCOPES,
    }))
}

async fn oauth_protected_resource_metadata(headers: HeaderMap) -> Json<Value> {
    let base = request_base_url(&headers);
    Json(json!({
        "resource": format!("{base}{MCP_PATH}"),
        "authorization_servers": [base],
        "scopes_supported": OAUTH_SCOPES,
    }))
}

async fn oauth_authorize_get(
    state: State<AppState>,
    query: Query<super::auth::AuthorizeParams>,
) -> Result<impl IntoResponse, AppError> {
    super::auth::authorize_form(state, query).await
}

async fn oauth_authorize_post(
    state: State<AppState>,
    form: Form<super::auth::AuthorizeSubmit>,
) -> Result<impl IntoResponse, AppError> {
    super::auth::authorize_submit(state, form).await
}

async fn oauth_token(State(state): State<AppState>, headers: HeaderMap, body: Bytes) -> Response {
    let token_request = match parse_token_request(&headers, &body) {
        Ok(request) => request,
        Err(message) => {
            return oauth_error_response(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                &message,
                None,
            );
        }
    };

    match super::auth::token(State(state), Json(token_request)).await {
        Ok(Json(tokens)) => (StatusCode::OK, Json(tokens)).into_response(),
        Err(err) => app_error_to_oauth_response(err),
    }
}

#[derive(Debug, Deserialize)]
struct DynamicClientRegistrationRequest {
    redirect_uris: Vec<String>,
    #[serde(default)]
    grant_types: Vec<String>,
    #[serde(default)]
    response_types: Vec<String>,
    #[serde(default)]
    token_endpoint_auth_method: Option<String>,
}

#[derive(Debug, Serialize)]
struct DynamicClientRegistrationResponse {
    client_id: String,
    client_id_issued_at: i64,
    redirect_uris: Vec<String>,
    grant_types: Vec<String>,
    response_types: Vec<String>,
    token_endpoint_auth_method: String,
}

async fn oauth_register(
    State(state): State<AppState>,
    Json(req): Json<DynamicClientRegistrationRequest>,
) -> Result<(StatusCode, Json<DynamicClientRegistrationResponse>), AppError> {
    if req.redirect_uris.is_empty() {
        return Err(AppError::Validation {
            message: "redirect_uris must not be empty".to_string(),
            field: Some("redirect_uris".to_string()),
            received: None,
            docs_hint: Some("Provide at least one HTTPS redirect URI.".to_string()),
        });
    }

    let mut normalized_redirects = Vec::with_capacity(req.redirect_uris.len());
    for redirect in req.redirect_uris {
        let parsed = Url::parse(redirect.trim()).map_err(|_| AppError::Validation {
            message: "redirect_uri is invalid".to_string(),
            field: Some("redirect_uris".to_string()),
            received: Some(Value::String(redirect.clone())),
            docs_hint: Some("Use a valid absolute URI.".to_string()),
        })?;

        let is_https = parsed.scheme() == "https";
        let is_loopback_http = parsed.scheme() == "http"
            && matches!(
                parsed.host_str(),
                Some("localhost") | Some("127.0.0.1") | Some("::1")
            );

        if !is_https && !is_loopback_http {
            return Err(AppError::Validation {
                message: "redirect_uri must use https or loopback http".to_string(),
                field: Some("redirect_uris".to_string()),
                received: Some(Value::String(redirect)),
                docs_hint: Some(
                    "Use HTTPS, or localhost/127.0.0.1 for native callbacks.".to_string(),
                ),
            });
        }

        normalized_redirects.push(parsed.to_string());
    }

    normalized_redirects.sort();
    normalized_redirects.dedup();

    let client_id = format!("kura-mcp-{}", Uuid::now_v7().simple());
    sqlx::query(
        "INSERT INTO oauth_clients (client_id, allowed_redirect_uris, allow_loopback_redirect, is_active) \
         VALUES ($1, $2, FALSE, TRUE)",
    )
    .bind(&client_id)
    .bind(&normalized_redirects)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    let grant_types = if req.grant_types.is_empty() {
        vec![
            "authorization_code".to_string(),
            "refresh_token".to_string(),
        ]
    } else {
        req.grant_types
    };

    let response_types = if req.response_types.is_empty() {
        vec!["code".to_string()]
    } else {
        req.response_types
    };

    let token_endpoint_auth_method = req
        .token_endpoint_auth_method
        .unwrap_or_else(|| "none".to_string());

    Ok((
        StatusCode::CREATED,
        Json(DynamicClientRegistrationResponse {
            client_id,
            client_id_issued_at: chrono::Utc::now().timestamp(),
            redirect_uris: normalized_redirects,
            grant_types,
            response_types,
            token_endpoint_auth_method,
        }),
    ))
}

fn parse_token_request(
    headers: &HeaderMap,
    body: &[u8],
) -> Result<super::auth::TokenRequest, String> {
    let content_type = headers
        .get(CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_ascii_lowercase();

    if content_type.contains("application/json") {
        return serde_json::from_slice(body)
            .map_err(|_| "Invalid JSON token request body.".to_string());
    }

    let params: HashMap<String, String> = url::form_urlencoded::parse(body).into_owned().collect();
    let grant_type = params
        .get("grant_type")
        .map(String::as_str)
        .unwrap_or_default();

    match grant_type {
        "authorization_code" => Ok(super::auth::TokenRequest::AuthorizationCode {
            code: required_form_param(&params, "code")?,
            code_verifier: required_form_param(&params, "code_verifier")?,
            redirect_uri: required_form_param(&params, "redirect_uri")?,
            client_id: required_form_param(&params, "client_id")?,
        }),
        "refresh_token" => Ok(super::auth::TokenRequest::RefreshToken {
            refresh_token: required_form_param(&params, "refresh_token")?,
            client_id: required_form_param(&params, "client_id")?,
        }),
        _ => {
            Err("Unsupported grant_type. Expected authorization_code or refresh_token.".to_string())
        }
    }
}

fn required_form_param(params: &HashMap<String, String>, key: &str) -> Result<String, String> {
    let value = params.get(key).map(String::as_str).unwrap_or("").trim();
    if value.is_empty() {
        return Err(format!("Missing required form field '{key}'"));
    }
    Ok(value.to_string())
}

fn app_error_to_oauth_response(err: AppError) -> Response {
    match err {
        AppError::Validation {
            message, docs_hint, ..
        } => oauth_error_response(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            &with_docs_hint(message, docs_hint),
            None,
        ),
        AppError::Unauthorized { message, docs_hint } => oauth_error_response(
            StatusCode::BAD_REQUEST,
            "invalid_grant",
            &with_docs_hint(message, docs_hint),
            None,
        ),
        AppError::Forbidden { message, docs_hint } => oauth_error_response(
            StatusCode::BAD_REQUEST,
            "access_denied",
            &with_docs_hint(message, docs_hint),
            None,
        ),
        AppError::RateLimited { retry_after_secs } => oauth_error_response(
            StatusCode::TOO_MANY_REQUESTS,
            "slow_down",
            "Rate limit exceeded. Retry later.",
            Some(retry_after_secs),
        ),
        _ => oauth_error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "server_error",
            "Internal server error.",
            None,
        ),
    }
}

fn with_docs_hint(message: String, docs_hint: Option<String>) -> String {
    if let Some(hint) = docs_hint {
        format!("{message} {hint}")
    } else {
        message
    }
}

fn oauth_error_response(
    status: StatusCode,
    code: &str,
    description: &str,
    retry_after: Option<u64>,
) -> Response {
    let mut response = (
        status,
        Json(json!({
            "error": code,
            "error_description": description,
        })),
    )
        .into_response();

    if let Some(seconds) = retry_after {
        if let Ok(value) = HeaderValue::from_str(&seconds.to_string()) {
            response.headers_mut().insert("retry-after", value);
        }
    }

    response
}

fn extract_bearer_token(headers: &HeaderMap) -> Result<String, &'static str> {
    let Some(raw) = headers
        .get(AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
    else {
        return Err("Missing access token");
    };

    let mut parts = raw.splitn(2, ' ');
    let scheme = parts.next().unwrap_or_default();
    let token = parts.next().unwrap_or_default().trim();

    if !scheme.eq_ignore_ascii_case("bearer") {
        return Err("Invalid authorization scheme");
    }
    if token.is_empty() {
        return Err("Missing access token");
    }
    Ok(token.to_string())
}

fn mcp_oauth_challenge(base_url: &str, description: &str) -> Response {
    let resource_metadata = format!("{base_url}/.well-known/oauth-protected-resource/mcp");
    let description = description.replace('"', "'");
    let challenge = format!(
        "Bearer realm=\"kura-mcp\", error=\"invalid_token\", error_description=\"{description}\", resource_metadata=\"{resource_metadata}\""
    );
    let mut response = (
        StatusCode::UNAUTHORIZED,
        Json(json!({
            "error": "invalid_token",
            "error_description": description,
        })),
    )
        .into_response();
    if let Ok(value) = HeaderValue::from_str(&challenge) {
        response.headers_mut().insert(WWW_AUTHENTICATE, value);
    }
    response
}

fn runtime_api_base_url() -> String {
    if let Ok(value) = std::env::var("KURA_MCP_API_URL") {
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return trimmed.trim_end_matches('/').to_string();
        }
    }
    let port = std::env::var("PORT").unwrap_or_else(|_| "3000".to_string());
    format!("http://127.0.0.1:{}", port.trim())
}

fn request_base_url(headers: &HeaderMap) -> String {
    let forwarded_proto = first_header_token(headers, "x-forwarded-proto");
    let forwarded_host = first_header_token(headers, "x-forwarded-host");
    let host = forwarded_host.or_else(|| {
        headers
            .get(HOST)
            .and_then(|v| v.to_str().ok())
            .map(ToOwned::to_owned)
    });

    if let Some(host) = host {
        let proto = forwarded_proto.unwrap_or_else(|| {
            if host.contains("localhost") || host.starts_with("127.0.0.1") {
                "http".to_string()
            } else {
                "https".to_string()
            }
        });
        return format!("{}://{}", proto.trim_end_matches(':'), host);
    }

    runtime_api_base_url()
}

fn first_header_token(headers: &HeaderMap, key: &str) -> Option<String> {
    headers
        .get(key)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.split(',').next())
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(ToOwned::to_owned)
}
