use std::collections::HashMap;

use axum::body::Bytes;
use axum::extract::{OriginalUri, Query, Request, State};
use axum::http::header::{AUTHORIZATION, CONTENT_TYPE, HOST, WWW_AUTHENTICATE};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::middleware::Next;
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
const OAUTH_REQUEST_ID_HEADER: &str = "x-kura-oauth-request-id";
const DCR_TOKEN_AUTH_METHODS: [&str; 2] = ["none", "client_secret_post"];

pub fn router() -> Router<AppState> {
    Router::new()
        .route(MCP_PATH, post(mcp_post).get(mcp_get))
        .route(
            "/.well-known/oauth-authorization-server",
            get(oauth_authorization_server_metadata),
        )
        .route(
            "/.well-known/oauth-authorization-server/",
            get(oauth_authorization_server_metadata),
        )
        .route(
            "/.well-known/oauth-authorization-server/mcp",
            get(oauth_authorization_server_metadata),
        )
        .route(
            "/.well-known/oauth-authorization-server/mcp/",
            get(oauth_authorization_server_metadata),
        )
        // Compatibility aliases for clients that resolve well-known from the MCP path.
        .route(
            "/mcp/.well-known/oauth-authorization-server",
            get(oauth_authorization_server_metadata),
        )
        .route(
            "/mcp/.well-known/oauth-authorization-server/",
            get(oauth_authorization_server_metadata),
        )
        .route(
            "/mcp/.well-known/oauth-authorization-server/mcp",
            get(oauth_authorization_server_metadata),
        )
        .route(
            "/mcp/.well-known/oauth-authorization-server/mcp/",
            get(oauth_authorization_server_metadata),
        )
        .route(
            "/.well-known/oauth-protected-resource",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/.well-known/oauth-protected-resource/",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/.well-known/oauth-protected-resource/mcp",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/.well-known/oauth-protected-resource/mcp/",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/mcp/.well-known/oauth-protected-resource",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/mcp/.well-known/oauth-protected-resource/",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/mcp/.well-known/oauth-protected-resource/mcp",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/mcp/.well-known/oauth-protected-resource/mcp/",
            get(oauth_protected_resource_metadata),
        )
        .route(
            "/oauth/authorize",
            get(oauth_authorize_get).post(oauth_authorize_post),
        )
        .route(
            "/oauth/authorize/",
            get(oauth_authorize_get).post(oauth_authorize_post),
        )
        .route(
            "/mcp/oauth/authorize",
            get(oauth_authorize_get).post(oauth_authorize_post),
        )
        .route(
            "/mcp/oauth/authorize/",
            get(oauth_authorize_get).post(oauth_authorize_post),
        )
        .route("/oauth/token", post(oauth_token))
        .route("/oauth/token/", post(oauth_token))
        .route("/mcp/oauth/token", post(oauth_token))
        .route("/mcp/oauth/token/", post(oauth_token))
        .route("/oauth/revoke", post(oauth_revoke))
        .route("/oauth/revoke/", post(oauth_revoke))
        .route("/mcp/oauth/revoke", post(oauth_revoke))
        .route("/mcp/oauth/revoke/", post(oauth_revoke))
        .route("/oauth/device/start", post(oauth_device_start))
        .route("/oauth/device/start/", post(oauth_device_start))
        .route("/mcp/oauth/device/start", post(oauth_device_start))
        .route("/mcp/oauth/device/start/", post(oauth_device_start))
        .route("/oauth/register", post(oauth_register))
        .route("/oauth/register/", post(oauth_register))
        .route("/mcp/oauth/register", post(oauth_register))
        .route("/mcp/oauth/register/", post(oauth_register))
        .layer(axum::middleware::from_fn(log_oauth_http_flow))
}

async fn mcp_get() -> Response {
    StatusCode::METHOD_NOT_ALLOWED.into_response()
}

async fn log_oauth_http_flow(req: Request, next: Next) -> Response {
    let method = req.method().to_string();
    let path = req.uri().path().to_string();
    let should_log = is_oauth_observed_path(&path);

    let origin = header_value(req.headers(), "origin");
    let user_agent = header_value(req.headers(), "user-agent");
    let forwarded_for = first_header_token(req.headers(), "x-forwarded-for");
    let content_type = header_value(req.headers(), "content-type");
    let ac_request_method = header_value(req.headers(), "access-control-request-method");
    let ac_request_headers = header_value(req.headers(), "access-control-request-headers");

    if should_log {
        tracing::info!(
            event = "mcp_oauth_http_request",
            method = %method,
            path = %path,
            origin = ?origin,
            user_agent = ?user_agent,
            forwarded_for = ?forwarded_for,
            content_type = ?content_type,
            access_control_request_method = ?ac_request_method,
            access_control_request_headers = ?ac_request_headers,
            "MCP OAuth HTTP request received"
        );
    }

    let response = next.run(req).await;

    if should_log {
        tracing::info!(
            event = "mcp_oauth_http_response",
            method = %method,
            path = %path,
            status = response.status().as_u16(),
            content_type = ?header_value(response.headers(), "content-type"),
            access_control_allow_origin = ?header_value(response.headers(), "access-control-allow-origin"),
            access_control_allow_methods = ?header_value(response.headers(), "access-control-allow-methods"),
            allow = ?header_value(response.headers(), "allow"),
            "MCP OAuth HTTP response sent"
        );
    }

    response
}

fn is_oauth_observed_path(path: &str) -> bool {
    path.starts_with("/oauth/")
        || path.starts_with("/mcp/oauth/")
        || path.contains("/.well-known/oauth-")
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

async fn oauth_authorization_server_metadata(
    headers: HeaderMap,
    original_uri: OriginalUri,
) -> Json<Value> {
    let base = request_base_url(&headers);
    log_oauth_discovery_request("authorization_server", &headers, &original_uri, &base);
    Json(json!({
        "issuer": base,
        "authorization_endpoint": format!("{base}/oauth/authorize"),
        "token_endpoint": format!("{base}/oauth/token"),
        "registration_endpoint": format!("{base}/oauth/register"),
        "revocation_endpoint": format!("{base}/oauth/revoke"),
        "device_authorization_endpoint": format!("{base}/oauth/device/start"),
        "response_types_supported": ["code"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "urn:ietf:params:oauth:grant-type:device_code"
        ],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": DCR_TOKEN_AUTH_METHODS,
        "scopes_supported": OAUTH_SCOPES,
    }))
}

async fn oauth_protected_resource_metadata(
    headers: HeaderMap,
    original_uri: OriginalUri,
) -> Json<Value> {
    let base = request_base_url(&headers);
    log_oauth_discovery_request("protected_resource", &headers, &original_uri, &base);
    Json(json!({
        "resource": format!("{base}{MCP_PATH}"),
        "authorization_servers": [base],
        "scopes_supported": OAUTH_SCOPES,
    }))
}

async fn oauth_authorize_get(
    state: State<AppState>,
    headers: HeaderMap,
    query: Query<super::auth::AuthorizeParams>,
) -> Result<impl IntoResponse, AppError> {
    super::auth::authorize_form(state, headers, query).await
}

async fn oauth_authorize_post(
    state: State<AppState>,
    headers: HeaderMap,
    form: Form<super::auth::AuthorizeSubmit>,
) -> Result<impl IntoResponse, AppError> {
    super::auth::authorize_submit(state, headers, form).await
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

async fn oauth_revoke() -> Response {
    // RFC 7009: revocation endpoint should return 200 even for unknown tokens.
    StatusCode::OK.into_response()
}

async fn oauth_device_start(
    state: State<AppState>,
    Json(req): Json<super::auth::DeviceAuthorizeRequest>,
) -> Result<impl IntoResponse, AppError> {
    super::auth::device_authorize(state, Json(req)).await
}

#[derive(Debug, Deserialize)]
struct DynamicClientRegistrationRequest {
    redirect_uris: Vec<String>,
    #[serde(default)]
    client_name: Option<String>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    client_name: Option<String>,
    grant_types: Vec<String>,
    response_types: Vec<String>,
    code_challenge_methods_supported: Vec<String>,
    token_endpoint_auth_method: String,
}

async fn oauth_register(
    State(state): State<AppState>,
    headers: HeaderMap,
    original_uri: OriginalUri,
    body: Bytes,
) -> Response {
    let log_context = oauth_log_context(&headers, &original_uri);
    let req: DynamicClientRegistrationRequest = match serde_json::from_slice(&body) {
        Ok(payload) => payload,
        Err(err) => {
            tracing::warn!(
                event = "mcp_oauth_dcr_rejected",
                request_id = %log_context.request_id,
                path = %log_context.path,
                origin = ?log_context.origin,
                user_agent = ?log_context.user_agent,
                forwarded_for = ?log_context.forwarded_for,
                reason = "invalid_json_body",
                body_len = body.len(),
                parse_error = %err,
                "MCP OAuth dynamic client registration rejected"
            );
            let response = oauth_error_response(
                StatusCode::BAD_REQUEST,
                "invalid_client_metadata",
                "Request body must be valid JSON.",
                None,
            );
            return with_oauth_request_id_header(response, &log_context.request_id);
        }
    };

    tracing::info!(
        event = "mcp_oauth_dcr_attempt",
        request_id = %log_context.request_id,
        path = %log_context.path,
        origin = ?log_context.origin,
        user_agent = ?log_context.user_agent,
        forwarded_for = ?log_context.forwarded_for,
        redirect_uri_count = req.redirect_uris.len(),
        redirect_uri_targets = ?summarize_redirect_uri_targets(&req.redirect_uris),
        client_name = ?req.client_name,
        grant_types = ?req.grant_types,
        response_types = ?req.response_types,
        token_endpoint_auth_method = ?req.token_endpoint_auth_method,
        "MCP OAuth dynamic client registration attempt"
    );

    match oauth_register_inner(&state, req, &log_context).await {
        Ok(registration) => {
            let response = (StatusCode::CREATED, Json(registration)).into_response();
            with_oauth_request_id_header(response, &log_context.request_id)
        }
        Err(err) => {
            let response = app_error_to_oauth_response(err);
            tracing::warn!(
                event = "mcp_oauth_dcr_failed",
                request_id = %log_context.request_id,
                path = %log_context.path,
                origin = ?log_context.origin,
                user_agent = ?log_context.user_agent,
                forwarded_for = ?log_context.forwarded_for,
                status = response.status().as_u16(),
                "MCP OAuth dynamic client registration failed"
            );
            with_oauth_request_id_header(response, &log_context.request_id)
        }
    }
}

async fn oauth_register_inner(
    state: &AppState,
    req: DynamicClientRegistrationRequest,
    log_context: &OauthLogContext,
) -> Result<DynamicClientRegistrationResponse, AppError> {
    if req.redirect_uris.is_empty() {
        tracing::warn!(
            event = "mcp_oauth_dcr_validation_failed",
            request_id = %log_context.request_id,
            path = %log_context.path,
            origin = ?log_context.origin,
            user_agent = ?log_context.user_agent,
            forwarded_for = ?log_context.forwarded_for,
            reason = "missing_redirect_uris",
            "MCP OAuth dynamic client registration validation failed"
        );
        return Err(AppError::Validation {
            message: "redirect_uris must not be empty".to_string(),
            field: Some("redirect_uris".to_string()),
            received: None,
            docs_hint: Some("Provide at least one HTTPS redirect URI.".to_string()),
        });
    }

    let mut normalized_redirects = Vec::with_capacity(req.redirect_uris.len());
    for redirect in req.redirect_uris {
        let parsed = Url::parse(redirect.trim()).map_err(|_| {
            tracing::warn!(
                event = "mcp_oauth_dcr_validation_failed",
                request_id = %log_context.request_id,
                path = %log_context.path,
                origin = ?log_context.origin,
                user_agent = ?log_context.user_agent,
                forwarded_for = ?log_context.forwarded_for,
                reason = "invalid_redirect_uri",
                redirect_uri = %redirect,
                "MCP OAuth dynamic client registration validation failed"
            );
            AppError::Validation {
                message: "redirect_uri is invalid".to_string(),
                field: Some("redirect_uris".to_string()),
                received: Some(Value::String(redirect.clone())),
                docs_hint: Some("Use a valid absolute URI.".to_string()),
            }
        })?;

        let is_https = parsed.scheme() == "https";
        let is_loopback_http = parsed.scheme() == "http"
            && matches!(
                parsed.host_str(),
                Some("localhost") | Some("127.0.0.1") | Some("::1")
            );

        if !is_https && !is_loopback_http {
            tracing::warn!(
                event = "mcp_oauth_dcr_validation_failed",
                request_id = %log_context.request_id,
                path = %log_context.path,
                origin = ?log_context.origin,
                user_agent = ?log_context.user_agent,
                forwarded_for = ?log_context.forwarded_for,
                reason = "redirect_scheme_not_allowed",
                redirect_uri = %redirect,
                "MCP OAuth dynamic client registration validation failed"
            );
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
    if !DCR_TOKEN_AUTH_METHODS
        .iter()
        .any(|method| method == &token_endpoint_auth_method)
    {
        tracing::warn!(
            event = "mcp_oauth_dcr_validation_failed",
            request_id = %log_context.request_id,
            path = %log_context.path,
            origin = ?log_context.origin,
            user_agent = ?log_context.user_agent,
            forwarded_for = ?log_context.forwarded_for,
            reason = "unsupported_token_endpoint_auth_method",
            token_endpoint_auth_method = %token_endpoint_auth_method,
            "MCP OAuth dynamic client registration validation failed"
        );
        return Err(AppError::Validation {
            message: "token_endpoint_auth_method is not supported".to_string(),
            field: Some("token_endpoint_auth_method".to_string()),
            received: Some(Value::String(token_endpoint_auth_method)),
            docs_hint: Some("Supported methods: none, client_secret_post.".to_string()),
        });
    }

    let client_name = req
        .client_name
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());

    let client_id = format!("kura-mcp-{}", Uuid::now_v7().simple());
    if let Err(err) = sqlx::query(
        "INSERT INTO oauth_clients (client_id, allowed_redirect_uris, allow_loopback_redirect, is_active) \
         VALUES ($1, $2, FALSE, TRUE)",
    )
    .bind(&client_id)
    .bind(&normalized_redirects)
    .execute(&state.db)
    .await
    {
        tracing::error!(
            event = "mcp_oauth_dcr_database_error",
            request_id = %log_context.request_id,
            path = %log_context.path,
            origin = ?log_context.origin,
            user_agent = ?log_context.user_agent,
            forwarded_for = ?log_context.forwarded_for,
            error = %err,
            "MCP OAuth dynamic client registration database insert failed"
        );
        return Err(AppError::Database(err));
    }

    tracing::info!(
        event = "mcp_oauth_dcr_success",
        request_id = %log_context.request_id,
        path = %log_context.path,
        origin = ?log_context.origin,
        user_agent = ?log_context.user_agent,
        forwarded_for = ?log_context.forwarded_for,
        client_id = %client_id,
        client_name = ?client_name,
        redirect_uri_count = normalized_redirects.len(),
        redirect_uri_targets = ?summarize_redirect_uri_targets(&normalized_redirects),
        "MCP OAuth dynamic client registration succeeded"
    );

    Ok(DynamicClientRegistrationResponse {
        client_id,
        client_id_issued_at: chrono::Utc::now().timestamp(),
        redirect_uris: normalized_redirects,
        client_name,
        grant_types,
        response_types,
        code_challenge_methods_supported: vec!["S256".to_string()],
        token_endpoint_auth_method,
    })
}

#[derive(Debug)]
struct OauthLogContext {
    request_id: String,
    path: String,
    origin: Option<String>,
    user_agent: Option<String>,
    forwarded_for: Option<String>,
}

fn oauth_log_context(headers: &HeaderMap, original_uri: &OriginalUri) -> OauthLogContext {
    OauthLogContext {
        request_id: format!("oauth-{}", Uuid::now_v7()),
        path: original_uri.0.path().to_string(),
        origin: header_value(headers, "origin"),
        user_agent: header_value(headers, "user-agent"),
        forwarded_for: first_header_token(headers, "x-forwarded-for"),
    }
}

fn log_oauth_discovery_request(
    metadata_kind: &'static str,
    headers: &HeaderMap,
    original_uri: &OriginalUri,
    base_url: &str,
) {
    tracing::info!(
        event = "mcp_oauth_discovery_request",
        metadata_kind = metadata_kind,
        path = %original_uri.0.path(),
        base_url = %base_url,
        origin = ?header_value(headers, "origin"),
        user_agent = ?header_value(headers, "user-agent"),
        forwarded_for = ?first_header_token(headers, "x-forwarded-for"),
        "MCP OAuth discovery metadata served"
    );
}

fn summarize_redirect_uri_targets(redirect_uris: &[String]) -> Vec<String> {
    let mut targets: Vec<String> = redirect_uris
        .iter()
        .filter_map(|value| {
            let parsed = Url::parse(value.trim()).ok()?;
            let host = parsed.host_str()?;
            let mut target = format!("{}://{}", parsed.scheme(), host);
            if let Some(port) = parsed.port() {
                target.push(':');
                target.push_str(&port.to_string());
            }
            Some(target)
        })
        .collect();
    targets.sort();
    targets.dedup();
    targets
}

fn with_oauth_request_id_header(mut response: Response, request_id: &str) -> Response {
    if let Ok(value) = HeaderValue::from_str(request_id) {
        response
            .headers_mut()
            .insert(OAUTH_REQUEST_ID_HEADER, value);
    }
    response
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

fn header_value(headers: &HeaderMap, key: &str) -> Option<String> {
    headers
        .get(key)
        .and_then(|value| value.to_str().ok())
        .map(ToOwned::to_owned)
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
