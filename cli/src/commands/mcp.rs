use std::collections::{BTreeMap, HashSet};

use clap::{Args, Subcommand};
use reqwest::Method;
use serde_json::{Map, Value, json};
use tokio::io::{self, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use uuid::Uuid;

use crate::util::{client, resolve_token};

const MCP_PROTOCOL_VERSION: &str = "2024-11-05";
const MCP_SERVER_NAME: &str = "kura-mcp";

#[derive(Subcommand)]
pub enum McpCommands {
    /// Run a Kura MCP server over stdio
    Serve(McpServeArgs),
}

#[derive(Args, Clone, Debug)]
pub struct McpServeArgs {
    /// Disable auth header injection (useful behind auth proxies)
    #[arg(long)]
    pub no_auth: bool,
    /// Explicit bearer token override (otherwise KURA_API_KEY or OAuth store)
    #[arg(long, env = "KURA_MCP_TOKEN")]
    pub token: Option<String>,
    /// Default metadata.source for events written via MCP
    #[arg(long, default_value = "mcp")]
    pub default_source: String,
    /// Default metadata.agent for events written via MCP
    #[arg(long, default_value = "kura-mcp")]
    pub default_agent: String,
}

pub async fn run(api_url: &str, inherited_no_auth: bool, command: McpCommands) -> i32 {
    match command {
        McpCommands::Serve(args) => {
            let server = McpServer::new(McpRuntimeConfig {
                api_url: api_url.to_string(),
                no_auth: inherited_no_auth || args.no_auth,
                explicit_token: args.token,
                default_source: args.default_source,
                default_agent: args.default_agent,
            });
            match server.serve_stdio().await {
                Ok(()) => 0,
                Err(err) => {
                    let payload = json!({
                        "error": "mcp_server_error",
                        "message": err,
                    });
                    eprintln!("{}", to_pretty_json(&payload));
                    1
                }
            }
        }
    }
}

#[derive(Clone, Debug)]
struct McpRuntimeConfig {
    api_url: String,
    no_auth: bool,
    explicit_token: Option<String>,
    default_source: String,
    default_agent: String,
}

struct McpServer {
    config: McpRuntimeConfig,
    http: reqwest::Client,
}

impl McpServer {
    fn new(config: McpRuntimeConfig) -> Self {
        Self {
            config,
            http: client(),
        }
    }

    async fn serve_stdio(&self) -> Result<(), String> {
        let stdin = io::stdin();
        let mut reader = BufReader::new(stdin);
        let mut stdout = io::stdout();

        loop {
            let incoming = read_framed_json(&mut reader)
                .await
                .map_err(|e| format!("Failed to read MCP message: {e}"))?;
            let Some(incoming) = incoming else {
                break;
            };

            let responses = self.handle_incoming_message(incoming).await;
            for response in responses {
                write_framed_json(&mut stdout, &response)
                    .await
                    .map_err(|e| format!("Failed to write MCP response: {e}"))?;
            }
        }

        Ok(())
    }

    async fn handle_incoming_message(&self, incoming: Value) -> Vec<Value> {
        let mut responses = Vec::new();

        if let Some(batch) = incoming.as_array() {
            if batch.is_empty() {
                responses.push(error_response(
                    Value::Null,
                    RpcError::invalid_request("Batch request must not be empty"),
                ));
                return responses;
            }
            for item in batch {
                if let Some(response) = self.handle_single_message(item.clone()).await {
                    responses.push(response);
                }
            }
            return responses;
        }

        if let Some(response) = self.handle_single_message(incoming).await {
            responses.push(response);
        }
        responses
    }

    async fn handle_single_message(&self, incoming: Value) -> Option<Value> {
        let Some(obj) = incoming.as_object() else {
            return Some(error_response(
                Value::Null,
                RpcError::invalid_request("Request must be a JSON object"),
            ));
        };

        if obj.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
            let id = obj.get("id").cloned().unwrap_or(Value::Null);
            return Some(error_response(
                id,
                RpcError::invalid_request("jsonrpc must be '2.0'"),
            ));
        }

        let Some(method) = obj.get("method").and_then(Value::as_str) else {
            // Most likely a client response; server does not issue outbound requests.
            return None;
        };

        let params = obj.get("params").cloned().unwrap_or(Value::Null);
        if let Some(id) = obj.get("id").cloned() {
            let result = self.handle_request(method, params).await;
            Some(match result {
                Ok(payload) => success_response(id, payload),
                Err(err) => error_response(id, err),
            })
        } else {
            self.handle_notification(method, params).await;
            None
        }
    }

    async fn handle_notification(&self, method: &str, _params: Value) {
        if matches!(
            method,
            "notifications/initialized" | "notifications/cancelled"
        ) {
            return;
        }
        // Unknown notifications are intentionally ignored.
    }

    async fn handle_request(&self, method: &str, params: Value) -> Result<Value, RpcError> {
        match method {
            "initialize" => Ok(self.initialize_payload()),
            "ping" => Ok(json!({})),
            "tools/list" => Ok(self.tools_list_payload()),
            "tools/call" => self.handle_tools_call(params).await,
            "resources/list" => Ok(self.resources_list_payload()),
            "resources/read" => self.handle_resources_read(params).await,
            "prompts/list" => Ok(json!({ "prompts": [] })),
            _ => Err(RpcError::method_not_found(method)),
        }
    }

    fn initialize_payload(&self) -> Value {
        json!({
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {
                    "listChanged": false
                },
                "resources": {
                    "listChanged": false
                },
                "prompts": {
                    "listChanged": false
                }
            },
            "serverInfo": {
                "name": MCP_SERVER_NAME,
                "version": env!("CARGO_PKG_VERSION")
            },
            "instructions": "Start with kura_discover, read projections as source of truth, and prefer kura_events_write with mode=simulate before commit for higher confidence."
        })
    }

    fn tools_list_payload(&self) -> Value {
        let tools: Vec<Value> = tool_definitions()
            .into_iter()
            .map(|tool| {
                json!({
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                })
            })
            .collect();
        json!({ "tools": tools })
    }

    async fn handle_tools_call(&self, params: Value) -> Result<Value, RpcError> {
        let params = params
            .as_object()
            .ok_or_else(|| RpcError::invalid_params("tools/call params must be an object"))?;

        let name = params
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| RpcError::invalid_params("tools/call requires string field 'name'"))?;

        let args = match params.get("arguments") {
            Some(Value::Object(map)) => map.clone(),
            Some(Value::Null) | None => Map::new(),
            Some(_) => {
                return Err(RpcError::invalid_params(
                    "tools/call 'arguments' must be an object",
                ));
            }
        };

        let result = self.execute_tool(name, &args).await;
        Ok(match result {
            Ok(payload) => json!({
                "content": [{ "type": "text", "text": to_pretty_json(&payload) }],
                "structuredContent": payload
            }),
            Err(err) => {
                let payload = err.to_value();
                json!({
                    "isError": true,
                    "content": [{ "type": "text", "text": to_pretty_json(&payload) }],
                    "structuredContent": payload
                })
            }
        })
    }

    async fn execute_tool(
        &self,
        tool_name: &str,
        args: &Map<String, Value>,
    ) -> Result<Value, ToolError> {
        match tool_name {
            "kura_discover" => self.tool_discover(args).await,
            "kura_api_request" => self.tool_api_request(args).await,
            "kura_events_write" => self.tool_events_write(args).await,
            "kura_events_list" => self.tool_events_list(args).await,
            "kura_projection_get" => self.tool_projection_get(args).await,
            "kura_projection_list" => self.tool_projection_list(args).await,
            "kura_agent_context" => self.tool_agent_context(args).await,
            "kura_semantic_resolve" => self.tool_semantic_resolve(args).await,
            _ => Err(ToolError::new(
                "unknown_tool",
                format!("Unknown tool '{tool_name}'"),
            )),
        }
    }

    async fn tool_discover(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let include_openapi = arg_bool(args, "include_openapi", true)?;
        let compact_openapi = arg_bool(args, "compact_openapi", true)?;
        let include_system_config = arg_bool(args, "include_system_config", true)?;
        let include_agent_capabilities = arg_bool(args, "include_agent_capabilities", true)?;

        let mut payload = json!({
            "generated_at": chrono::Utc::now(),
            "api_url": self.config.api_url,
            "server": {
                "name": MCP_SERVER_NAME,
                "version": env!("CARGO_PKG_VERSION"),
                "protocol_version": MCP_PROTOCOL_VERSION
            }
        });
        let mut warnings = Vec::<String>::new();

        if include_openapi {
            let section = match self
                .send_api_request(
                    Method::GET,
                    "/api-doc/openapi.json",
                    &[],
                    None,
                    false,
                    false,
                )
                .await
            {
                Ok(result) => {
                    let mut section = result.to_value();
                    if compact_openapi && result.is_success() {
                        section["compact_endpoints"] =
                            Value::Array(extract_openapi_endpoints(&result.body));
                    }
                    section
                }
                Err(err) => {
                    warnings.push("Failed to fetch OpenAPI spec".to_string());
                    err.to_value()
                }
            };
            payload["openapi"] = section;
        }

        if include_agent_capabilities {
            let section = match self
                .send_api_request(
                    Method::GET,
                    "/v1/agent/capabilities",
                    &[],
                    None,
                    true,
                    false,
                )
                .await
            {
                Ok(result) => result.to_value(),
                Err(err) => {
                    warnings.push("Failed to fetch /v1/agent/capabilities".to_string());
                    err.to_value()
                }
            };
            payload["agent_capabilities"] = section;
        }

        if include_system_config {
            let section = match self
                .send_api_request(Method::GET, "/v1/system/config", &[], None, true, false)
                .await
            {
                Ok(result) => result.to_value(),
                Err(err) => {
                    warnings.push("Failed to fetch /v1/system/config".to_string());
                    err.to_value()
                }
            };
            payload["system_config"] = section;
        }

        if !warnings.is_empty() {
            payload["warnings"] = Value::Array(warnings.into_iter().map(Value::String).collect());
        }

        Ok(payload)
    }

    async fn tool_api_request(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let method_raw = required_string(args, "method")?;
        let path_raw = required_string(args, "path")?;
        let method = parse_http_method(&method_raw)?;
        let path = normalize_api_path(&path_raw)?;
        let query = parse_query_pairs(args.get("query"))?;
        let body = args.get("body").cloned();
        let include_headers = arg_bool(args, "include_headers", false)?;
        let auth_mode = arg_string(args, "auth_mode", "auto")?;
        let requires_auth = match auth_mode.as_str() {
            "required" => true,
            "none" => false,
            "auto" => path_requires_auth(&path),
            _ => {
                return Err(ToolError::new(
                    "validation_failed",
                    "auth_mode must be one of: auto, required, none",
                )
                .with_field("auth_mode"));
            }
        };

        let response = self
            .send_api_request(
                method.clone(),
                &path,
                &query,
                body,
                requires_auth,
                include_headers,
            )
            .await?;

        Ok(json!({
            "request": {
                "method": method.as_str(),
                "path": path,
                "query": pairs_to_json_object(&query),
                "auth_mode": auth_mode
            },
            "response": response.to_value(),
        }))
    }

    async fn tool_events_write(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let mode_raw = arg_string(args, "mode", "commit")?;
        let mode = parse_write_mode(&mode_raw)?;
        let strategy_raw = arg_string(args, "idempotency_strategy", "auto_if_missing")?;
        let strategy = parse_idempotency_strategy(&strategy_raw)?;

        let events_value = args.get("events").ok_or_else(|| {
            ToolError::new("validation_failed", "Missing required field 'events'")
                .with_field("events")
        })?;
        let events = events_value.as_array().ok_or_else(|| {
            ToolError::new("validation_failed", "'events' must be an array").with_field("events")
        })?;
        if events.is_empty() {
            return Err(
                ToolError::new("validation_failed", "'events' must not be empty")
                    .with_field("events"),
            );
        }

        let defaults = metadata_defaults_from_args(
            args.get("default_metadata"),
            &self.config.default_source,
            &self.config.default_agent,
        )?;
        let normalized_events = ensure_event_defaults(events, &defaults, strategy)?;

        let (path, body) = match mode {
            WriteMode::Commit => {
                if normalized_events.len() == 1 {
                    ("/v1/events".to_string(), normalized_events[0].clone())
                } else {
                    (
                        "/v1/events/batch".to_string(),
                        json!({ "events": normalized_events }),
                    )
                }
            }
            WriteMode::Simulate => (
                "/v1/events/simulate".to_string(),
                json!({ "events": normalized_events }),
            ),
            WriteMode::WriteWithProof => {
                let targets = parse_read_after_write_targets(args.get("read_after_write_targets"))?;
                let mut body = json!({
                    "events": normalized_events,
                    "read_after_write_targets": targets
                });
                if let Some(verify_timeout_ms) = arg_optional_u64(args, "verify_timeout_ms")? {
                    body["verify_timeout_ms"] = json!(verify_timeout_ms);
                }
                ("/v1/agent/write-with-proof".to_string(), body)
            }
        };

        let response = self
            .send_api_request(Method::POST, &path, &[], Some(body), true, false)
            .await?;

        Ok(json!({
            "request": {
                "mode": mode.as_str(),
                "path": path,
                "event_count": events.len()
            },
            "response": response.to_value()
        }))
    }

    async fn tool_events_list(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let mut query = Vec::new();
        if let Some(event_type) = arg_optional_string(args, "event_type")? {
            query.push(("event_type".to_string(), event_type));
        }
        if let Some(since) = arg_optional_string(args, "since")? {
            query.push(("since".to_string(), since));
        }
        if let Some(until) = arg_optional_string(args, "until")? {
            query.push(("until".to_string(), until));
        }
        if let Some(limit) = arg_optional_u64(args, "limit")? {
            query.push(("limit".to_string(), limit.to_string()));
        }
        if let Some(cursor) = arg_optional_string(args, "cursor")? {
            query.push(("cursor".to_string(), cursor));
        }

        let response = self
            .send_api_request(Method::GET, "/v1/events", &query, None, true, false)
            .await?;

        Ok(json!({
            "request": {
                "path": "/v1/events",
                "query": pairs_to_json_object(&query)
            },
            "response": response.to_value()
        }))
    }

    async fn tool_projection_get(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let projection_type = required_string(args, "projection_type")?;
        let key = required_string(args, "key")?;
        let path = format!("/v1/projections/{projection_type}/{key}");

        let response = self
            .send_api_request(Method::GET, &path, &[], None, true, false)
            .await?;

        Ok(json!({
            "request": {
                "path": path,
                "projection_type": projection_type,
                "key": key
            },
            "response": response.to_value()
        }))
    }

    async fn tool_projection_list(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let path = if let Some(projection_type) = arg_optional_string(args, "projection_type")? {
            format!("/v1/projections/{projection_type}")
        } else {
            "/v1/projections".to_string()
        };

        let response = self
            .send_api_request(Method::GET, &path, &[], None, true, false)
            .await?;

        Ok(json!({
            "request": { "path": path },
            "response": response.to_value()
        }))
    }

    async fn tool_agent_context(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let mut query = Vec::new();
        if let Some(limit) = arg_optional_u64(args, "exercise_limit")? {
            query.push(("exercise_limit".to_string(), limit.to_string()));
        }
        if let Some(limit) = arg_optional_u64(args, "strength_limit")? {
            query.push(("strength_limit".to_string(), limit.to_string()));
        }
        if let Some(limit) = arg_optional_u64(args, "custom_limit")? {
            query.push(("custom_limit".to_string(), limit.to_string()));
        }
        if let Some(task_intent) = arg_optional_string(args, "task_intent")? {
            query.push(("task_intent".to_string(), task_intent));
        }

        let response = self
            .send_api_request(Method::GET, "/v1/agent/context", &query, None, true, false)
            .await?;

        Ok(json!({
            "request": {
                "path": "/v1/agent/context",
                "query": pairs_to_json_object(&query)
            },
            "response": response.to_value()
        }))
    }

    async fn tool_semantic_resolve(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let queries = args
            .get("queries")
            .and_then(Value::as_array)
            .ok_or_else(|| {
                ToolError::new("validation_failed", "Missing required field 'queries'")
                    .with_field("queries")
            })?;
        if queries.is_empty() {
            return Err(
                ToolError::new("validation_failed", "'queries' must not be empty")
                    .with_field("queries"),
            );
        }

        let mut body = json!({
            "queries": queries
        });
        if let Some(top_k) = arg_optional_u64(args, "top_k")? {
            body["top_k"] = json!(top_k);
        }

        let response = self
            .send_api_request(
                Method::POST,
                "/v1/semantic/resolve",
                &[],
                Some(body),
                true,
                false,
            )
            .await?;

        Ok(json!({
            "request": {
                "path": "/v1/semantic/resolve",
                "query_count": queries.len()
            },
            "response": response.to_value()
        }))
    }

    fn resources_list_payload(&self) -> Value {
        let resources: Vec<Value> = resource_definitions()
            .into_iter()
            .map(|res| {
                json!({
                    "uri": res.uri,
                    "name": res.name,
                    "description": res.description,
                    "mimeType": "application/json"
                })
            })
            .collect();
        json!({ "resources": resources })
    }

    async fn handle_resources_read(&self, params: Value) -> Result<Value, RpcError> {
        let params = params
            .as_object()
            .ok_or_else(|| RpcError::invalid_params("resources/read params must be an object"))?;
        let uri = params.get("uri").and_then(Value::as_str).ok_or_else(|| {
            RpcError::invalid_params("resources/read requires string field 'uri'")
        })?;

        let content_payload = match uri {
            "kura://openapi" => self
                .send_api_request(
                    Method::GET,
                    "/api-doc/openapi.json",
                    &[],
                    None,
                    false,
                    false,
                )
                .await
                .map(|r| r.to_value())
                .map_err(|e| RpcError::internal(e.message))?,
            "kura://agent/capabilities" => self
                .send_api_request(
                    Method::GET,
                    "/v1/agent/capabilities",
                    &[],
                    None,
                    true,
                    false,
                )
                .await
                .map(|r| r.to_value())
                .map_err(|e| RpcError::internal(e.message))?,
            "kura://system/config" => self
                .send_api_request(Method::GET, "/v1/system/config", &[], None, true, false)
                .await
                .map(|r| r.to_value())
                .map_err(|e| RpcError::internal(e.message))?,
            "kura://discovery/summary" => self
                .tool_discover(&Map::new())
                .await
                .map_err(|e| RpcError::internal(e.message))?,
            _ => {
                return Err(RpcError::invalid_params(format!(
                    "Unknown resource uri '{uri}'"
                )));
            }
        };

        Ok(json!({
            "contents": [{
                "uri": uri,
                "mimeType": "application/json",
                "text": to_pretty_json(&content_payload)
            }]
        }))
    }

    async fn send_api_request(
        &self,
        method: Method,
        path: &str,
        query: &[(String, String)],
        body: Option<Value>,
        requires_auth: bool,
        include_headers: bool,
    ) -> Result<ApiCallResult, ToolError> {
        let path = normalize_api_path(path)?;
        let mut url = reqwest::Url::parse(&format!(
            "{}{}",
            self.config.api_url.trim_end_matches('/'),
            path
        ))
        .map_err(|e| ToolError::new("invalid_url", format!("Invalid API URL/path: {e}")))?;
        if !query.is_empty() {
            let mut qp = url.query_pairs_mut();
            for (k, v) in query {
                qp.append_pair(k, v);
            }
        }

        let mut request = self.http.request(method, url);
        if requires_auth && !self.config.no_auth {
            let token = self.resolve_bearer_token().await?;
            request = request.header("Authorization", format!("Bearer {token}"));
        }
        if let Some(body) = body {
            request = request.json(&body);
        }

        let response = request.send().await.map_err(|e| {
            ToolError::new(
                "connection_error",
                format!("Failed to reach Kura API at {}: {e}", self.config.api_url),
            )
            .with_docs_hint("Ensure the API is running and KURA_API_URL points to it.")
        })?;

        let status = response.status().as_u16();
        let headers = if include_headers {
            Some(
                response
                    .headers()
                    .iter()
                    .map(|(k, v)| (k.to_string(), v.to_str().unwrap_or("<binary>").to_string()))
                    .collect::<BTreeMap<_, _>>(),
            )
        } else {
            None
        };
        let bytes = response.bytes().await.map_err(|e| {
            ToolError::new(
                "response_error",
                format!("Failed to read API response body: {e}"),
            )
        })?;
        let body = parse_response_body(&bytes);

        Ok(ApiCallResult {
            status,
            body,
            headers,
        })
    }

    async fn resolve_bearer_token(&self) -> Result<String, ToolError> {
        if let Some(token) = &self.config.explicit_token {
            return Ok(token.clone());
        }
        resolve_token(&self.config.api_url).await.map_err(|e| {
            ToolError::new("auth_missing", e.to_string())
                .with_docs_hint("Run `kura login`, set KURA_API_KEY, or pass --token.")
        })
    }
}

#[derive(Debug)]
struct RpcError {
    code: i64,
    message: String,
    data: Option<Value>,
}

impl RpcError {
    fn invalid_request(message: impl Into<String>) -> Self {
        Self {
            code: -32600,
            message: message.into(),
            data: None,
        }
    }

    fn method_not_found(method: &str) -> Self {
        Self {
            code: -32601,
            message: format!("Method not found: {method}"),
            data: None,
        }
    }

    fn invalid_params(message: impl Into<String>) -> Self {
        Self {
            code: -32602,
            message: message.into(),
            data: None,
        }
    }

    fn internal(message: impl Into<String>) -> Self {
        Self {
            code: -32603,
            message: message.into(),
            data: None,
        }
    }
}

#[derive(Debug, Clone)]
struct ToolError {
    code: String,
    message: String,
    field: Option<String>,
    docs_hint: Option<String>,
    details: Option<Value>,
}

impl ToolError {
    fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            field: None,
            docs_hint: None,
            details: None,
        }
    }

    fn with_field(mut self, field: impl Into<String>) -> Self {
        self.field = Some(field.into());
        self
    }

    fn with_docs_hint(mut self, docs_hint: impl Into<String>) -> Self {
        self.docs_hint = Some(docs_hint.into());
        self
    }

    fn to_value(&self) -> Value {
        let mut payload = json!({
            "error": self.code,
            "message": self.message
        });
        if let Some(field) = &self.field {
            payload["field"] = Value::String(field.clone());
        }
        if let Some(docs_hint) = &self.docs_hint {
            payload["docs_hint"] = Value::String(docs_hint.clone());
        }
        if let Some(details) = &self.details {
            payload["details"] = details.clone();
        }
        payload
    }
}

#[derive(Debug)]
struct ApiCallResult {
    status: u16,
    body: Value,
    headers: Option<BTreeMap<String, String>>,
}

impl ApiCallResult {
    fn is_success(&self) -> bool {
        (200..=299).contains(&self.status)
    }

    fn to_value(&self) -> Value {
        let mut value = json!({
            "ok": self.is_success(),
            "status": self.status,
            "body": self.body
        });
        if let Some(headers) = &self.headers {
            value["headers"] = json!(headers);
        }
        value
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WriteMode {
    Commit,
    Simulate,
    WriteWithProof,
}

impl WriteMode {
    fn as_str(self) -> &'static str {
        match self {
            WriteMode::Commit => "commit",
            WriteMode::Simulate => "simulate",
            WriteMode::WriteWithProof => "write_with_proof",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum IdempotencyStrategy {
    Preserve,
    AutoIfMissing,
}

#[derive(Debug, Clone, Default)]
struct MetadataDefaults {
    source: Option<String>,
    agent: Option<String>,
    device: Option<String>,
    session_id: Option<String>,
}

#[derive(Debug)]
struct ToolDefinition {
    name: &'static str,
    description: &'static str,
    input_schema: Value,
}

#[derive(Debug)]
struct ResourceDefinition {
    uri: &'static str,
    name: &'static str,
    description: &'static str,
}

fn tool_definitions() -> Vec<ToolDefinition> {
    vec![
        ToolDefinition {
            name: "kura_discover",
            description: "Discover Kura capabilities, schemas, and endpoints.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "include_openapi": { "type": "boolean", "default": true },
                    "compact_openapi": { "type": "boolean", "default": true },
                    "include_system_config": { "type": "boolean", "default": true },
                    "include_agent_capabilities": { "type": "boolean", "default": true }
                },
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_api_request",
            description: "Generic API request fallback for non-hardcoded workflows.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "method": { "type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] },
                    "path": { "type": "string", "description": "Absolute API path like /v1/events" },
                    "query": {
                        "description": "Either object map or [{key,value}] entries.",
                        "oneOf": [
                            { "type": "object", "additionalProperties": { "type": ["string", "number", "boolean"] } },
                            {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "key": { "type": "string" },
                                        "value": { "type": ["string", "number", "boolean"] }
                                    },
                                    "required": ["key", "value"],
                                    "additionalProperties": false
                                }
                            }
                        ]
                    },
                    "body": {},
                    "auth_mode": { "type": "string", "enum": ["auto", "required", "none"], "default": "auto" },
                    "include_headers": { "type": "boolean", "default": false }
                },
                "required": ["method", "path"],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_events_write",
            description: "Write or simulate events with metadata/idempotency guardrails.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "mode": { "type": "string", "enum": ["commit", "simulate", "write_with_proof"], "default": "commit" },
                    "events": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "timestamp": { "type": "string", "description": "RFC3339; defaults to now when omitted" },
                                "event_type": { "type": "string" },
                                "data": {},
                                "metadata": { "type": "object" }
                            },
                            "required": ["event_type", "data"]
                        }
                    },
                    "default_metadata": {
                        "type": "object",
                        "properties": {
                            "source": { "type": "string" },
                            "agent": { "type": "string" },
                            "device": { "type": "string" },
                            "session_id": { "type": "string" }
                        },
                        "additionalProperties": false
                    },
                    "idempotency_strategy": { "type": "string", "enum": ["auto_if_missing", "preserve"], "default": "auto_if_missing" },
                    "read_after_write_targets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "projection_type": { "type": "string" },
                                "key": { "type": "string" }
                            },
                            "required": ["projection_type", "key"],
                            "additionalProperties": false
                        }
                    },
                    "verify_timeout_ms": { "type": "integer", "minimum": 100, "maximum": 10000 }
                },
                "required": ["events"],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_events_list",
            description: "List events with cursor/time/type filters.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "event_type": { "type": "string" },
                    "since": { "type": "string", "description": "RFC3339 timestamp inclusive" },
                    "until": { "type": "string", "description": "RFC3339 timestamp exclusive" },
                    "limit": { "type": "integer", "minimum": 1, "maximum": 200 },
                    "cursor": { "type": "string" }
                },
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_projection_get",
            description: "Read one projection by type/key.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "projection_type": { "type": "string" },
                    "key": { "type": "string" }
                },
                "required": ["projection_type", "key"],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_projection_list",
            description: "List projections by type or full snapshot.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "projection_type": { "type": "string" }
                },
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_agent_context",
            description: "Fetch ranked context bundle for agent planning/writing.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "exercise_limit": { "type": "integer", "minimum": 1, "maximum": 100 },
                    "strength_limit": { "type": "integer", "minimum": 1, "maximum": 100 },
                    "custom_limit": { "type": "integer", "minimum": 1, "maximum": 100 },
                    "task_intent": { "type": "string" }
                },
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_semantic_resolve",
            description: "Resolve free-text exercise/food terms to canonical keys.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "term": { "type": "string" },
                                "domain": { "type": "string", "enum": ["exercise", "food"] }
                            },
                            "required": ["term", "domain"],
                            "additionalProperties": false
                        }
                    },
                    "top_k": { "type": "integer", "minimum": 1, "maximum": 10 }
                },
                "required": ["queries"],
                "additionalProperties": false
            }),
        },
    ]
}

fn resource_definitions() -> Vec<ResourceDefinition> {
    vec![
        ResourceDefinition {
            uri: "kura://openapi",
            name: "Kura OpenAPI Spec",
            description: "Live OpenAPI schema from /api-doc/openapi.json",
        },
        ResourceDefinition {
            uri: "kura://agent/capabilities",
            name: "Agent Capabilities Contract",
            description: "Current write/read protocol expectations for agents",
        },
        ResourceDefinition {
            uri: "kura://system/config",
            name: "System Config",
            description: "Global dimensions, conventions, and static agent config",
        },
        ResourceDefinition {
            uri: "kura://discovery/summary",
            name: "MCP Discovery Summary",
            description: "Convenience bundle: openapi + capabilities + system config",
        },
    ]
}

fn parse_http_method(raw: &str) -> Result<Method, ToolError> {
    match raw.trim().to_uppercase().as_str() {
        "GET" => Ok(Method::GET),
        "POST" => Ok(Method::POST),
        "PUT" => Ok(Method::PUT),
        "PATCH" => Ok(Method::PATCH),
        "DELETE" => Ok(Method::DELETE),
        "HEAD" => Ok(Method::HEAD),
        "OPTIONS" => Ok(Method::OPTIONS),
        _ => Err(ToolError::new(
            "validation_failed",
            format!("Unsupported HTTP method '{raw}'"),
        )
        .with_field("method")),
    }
}

fn normalize_api_path(raw: &str) -> Result<String, ToolError> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err(
            ToolError::new("validation_failed", "API path must not be empty").with_field("path"),
        );
    }
    if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
        return Err(ToolError::new(
            "validation_failed",
            "Pass API path only (e.g. /v1/events), not full URL",
        )
        .with_field("path"));
    }
    if trimmed.starts_with('/') {
        Ok(trimmed.to_string())
    } else {
        Ok(format!("/{trimmed}"))
    }
}

fn path_requires_auth(path: &str) -> bool {
    let p = path.trim().to_lowercase();
    !(p == "/health" || p.starts_with("/api-doc/") || p.starts_with("/swagger-ui"))
}

fn arg_bool(args: &Map<String, Value>, key: &str, default: bool) -> Result<bool, ToolError> {
    match args.get(key) {
        None => Ok(default),
        Some(Value::Bool(v)) => Ok(*v),
        Some(_) => Err(
            ToolError::new("validation_failed", format!("'{key}' must be a boolean"))
                .with_field(key),
        ),
    }
}

fn arg_string(args: &Map<String, Value>, key: &str, default: &str) -> Result<String, ToolError> {
    match args.get(key) {
        None => Ok(default.to_string()),
        Some(Value::String(v)) => Ok(v.clone()),
        Some(_) => Err(
            ToolError::new("validation_failed", format!("'{key}' must be a string"))
                .with_field(key),
        ),
    }
}

fn required_string(args: &Map<String, Value>, key: &str) -> Result<String, ToolError> {
    let value = args.get(key).ok_or_else(|| {
        ToolError::new(
            "validation_failed",
            format!("Missing required field '{key}'"),
        )
        .with_field(key)
    })?;
    match value {
        Value::String(v) if !v.trim().is_empty() => Ok(v.clone()),
        Value::String(_) => Err(ToolError::new(
            "validation_failed",
            format!("'{key}' must not be empty"),
        )
        .with_field(key)),
        _ => Err(
            ToolError::new("validation_failed", format!("'{key}' must be a string"))
                .with_field(key),
        ),
    }
}

fn arg_optional_string(args: &Map<String, Value>, key: &str) -> Result<Option<String>, ToolError> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(v)) if v.trim().is_empty() => Ok(None),
        Some(Value::String(v)) => Ok(Some(v.clone())),
        Some(_) => Err(
            ToolError::new("validation_failed", format!("'{key}' must be a string"))
                .with_field(key),
        ),
    }
}

fn arg_optional_u64(args: &Map<String, Value>, key: &str) -> Result<Option<u64>, ToolError> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => n
            .as_u64()
            .ok_or_else(|| {
                ToolError::new(
                    "validation_failed",
                    format!("'{key}' must be an unsigned integer"),
                )
                .with_field(key)
            })
            .map(Some),
        Some(_) => Err(ToolError::new(
            "validation_failed",
            format!("'{key}' must be an unsigned integer"),
        )
        .with_field(key)),
    }
}

fn parse_query_pairs(query_value: Option<&Value>) -> Result<Vec<(String, String)>, ToolError> {
    let Some(query_value) = query_value else {
        return Ok(Vec::new());
    };

    match query_value {
        Value::Object(map) => {
            let mut out = Vec::with_capacity(map.len());
            for (k, v) in map {
                if v.is_null() {
                    continue;
                }
                out.push((k.clone(), scalar_to_string(v, "query")?));
            }
            Ok(out)
        }
        Value::Array(items) => {
            let mut out = Vec::with_capacity(items.len());
            for (index, item) in items.iter().enumerate() {
                let obj = item.as_object().ok_or_else(|| {
                    ToolError::new(
                        "validation_failed",
                        format!("query[{index}] must be an object with key/value"),
                    )
                    .with_field("query")
                })?;
                let key = obj
                    .get("key")
                    .and_then(Value::as_str)
                    .filter(|s| !s.trim().is_empty())
                    .ok_or_else(|| {
                        ToolError::new(
                            "validation_failed",
                            format!("query[{index}].key must be a non-empty string"),
                        )
                        .with_field("query")
                    })?;
                let value = obj.get("value").ok_or_else(|| {
                    ToolError::new(
                        "validation_failed",
                        format!("query[{index}].value is required"),
                    )
                    .with_field("query")
                })?;
                out.push((key.to_string(), scalar_to_string(value, "query")?));
            }
            Ok(out)
        }
        _ => Err(ToolError::new(
            "validation_failed",
            "'query' must be an object map or [{key,value}] array",
        )
        .with_field("query")),
    }
}

fn scalar_to_string(value: &Value, field: &str) -> Result<String, ToolError> {
    match value {
        Value::String(v) => Ok(v.clone()),
        Value::Number(v) => Ok(v.to_string()),
        Value::Bool(v) => Ok(v.to_string()),
        _ => Err(ToolError::new(
            "validation_failed",
            format!("'{field}' values must be scalar (string/number/bool)"),
        )
        .with_field(field)),
    }
}

fn parse_write_mode(raw: &str) -> Result<WriteMode, ToolError> {
    match raw.trim().to_lowercase().as_str() {
        "commit" => Ok(WriteMode::Commit),
        "simulate" => Ok(WriteMode::Simulate),
        "write_with_proof" => Ok(WriteMode::WriteWithProof),
        _ => Err(ToolError::new(
            "validation_failed",
            "mode must be one of: commit, simulate, write_with_proof",
        )
        .with_field("mode")),
    }
}

fn parse_idempotency_strategy(raw: &str) -> Result<IdempotencyStrategy, ToolError> {
    match raw.trim().to_lowercase().as_str() {
        "preserve" => Ok(IdempotencyStrategy::Preserve),
        "auto_if_missing" => Ok(IdempotencyStrategy::AutoIfMissing),
        _ => Err(ToolError::new(
            "validation_failed",
            "idempotency_strategy must be 'auto_if_missing' or 'preserve'",
        )
        .with_field("idempotency_strategy")),
    }
}

fn metadata_defaults_from_args(
    value: Option<&Value>,
    fallback_source: &str,
    fallback_agent: &str,
) -> Result<MetadataDefaults, ToolError> {
    let mut defaults = MetadataDefaults {
        source: Some(fallback_source.to_string()),
        agent: Some(fallback_agent.to_string()),
        device: None,
        session_id: None,
    };

    let Some(value) = value else {
        return Ok(defaults);
    };
    let map = value.as_object().ok_or_else(|| {
        ToolError::new("validation_failed", "'default_metadata' must be an object")
            .with_field("default_metadata")
    })?;

    defaults.source = optional_string_in_map(map, "source", "default_metadata.source")?;
    defaults.agent = optional_string_in_map(map, "agent", "default_metadata.agent")?;
    defaults.device = optional_string_in_map(map, "device", "default_metadata.device")?;
    defaults.session_id = optional_string_in_map(map, "session_id", "default_metadata.session_id")?;

    Ok(defaults)
}

fn optional_string_in_map(
    map: &Map<String, Value>,
    key: &str,
    field: &str,
) -> Result<Option<String>, ToolError> {
    match map.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(v)) if v.trim().is_empty() => Ok(None),
        Some(Value::String(v)) => Ok(Some(v.clone())),
        Some(_) => Err(
            ToolError::new("validation_failed", format!("'{field}' must be a string"))
                .with_field(field),
        ),
    }
}

fn ensure_event_defaults(
    events: &[Value],
    defaults: &MetadataDefaults,
    strategy: IdempotencyStrategy,
) -> Result<Vec<Value>, ToolError> {
    let mut out = Vec::with_capacity(events.len());

    for (index, event) in events.iter().enumerate() {
        let event_obj = event.as_object().ok_or_else(|| {
            ToolError::new(
                "validation_failed",
                format!("events[{index}] must be an object"),
            )
            .with_field(format!("events[{index}]"))
        })?;
        let mut event_obj = event_obj.clone();

        let event_type = event_obj
            .get("event_type")
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or("");
        if event_type.is_empty() {
            return Err(ToolError::new(
                "validation_failed",
                format!("events[{index}].event_type is required"),
            )
            .with_field(format!("events[{index}].event_type")));
        }
        if !event_obj.contains_key("data") {
            return Err(ToolError::new(
                "validation_failed",
                format!("events[{index}].data is required"),
            )
            .with_field(format!("events[{index}].data")));
        }

        match event_obj.get("timestamp") {
            Some(Value::String(ts)) if !ts.trim().is_empty() => {}
            Some(Value::String(_)) | None | Some(Value::Null) => {
                event_obj.insert(
                    "timestamp".to_string(),
                    Value::String(chrono::Utc::now().to_rfc3339()),
                );
            }
            Some(_) => {
                return Err(ToolError::new(
                    "validation_failed",
                    format!("events[{index}].timestamp must be an RFC3339 string"),
                )
                .with_field(format!("events[{index}].timestamp")));
            }
        }

        let mut metadata = match event_obj.get("metadata") {
            None | Some(Value::Null) => Map::new(),
            Some(Value::Object(map)) => map.clone(),
            Some(_) => {
                return Err(ToolError::new(
                    "validation_failed",
                    format!("events[{index}].metadata must be an object"),
                )
                .with_field(format!("events[{index}].metadata")));
            }
        };

        set_default_metadata_string(
            &mut metadata,
            "source",
            defaults.source.as_deref(),
            &format!("events[{index}].metadata.source"),
        )?;
        set_default_metadata_string(
            &mut metadata,
            "agent",
            defaults.agent.as_deref(),
            &format!("events[{index}].metadata.agent"),
        )?;
        set_default_metadata_string(
            &mut metadata,
            "device",
            defaults.device.as_deref(),
            &format!("events[{index}].metadata.device"),
        )?;
        set_default_metadata_string(
            &mut metadata,
            "session_id",
            defaults.session_id.as_deref(),
            &format!("events[{index}].metadata.session_id"),
        )?;

        match metadata.get("idempotency_key") {
            Some(Value::String(v)) if !v.trim().is_empty() => {}
            Some(Value::String(_)) | None | Some(Value::Null) => match strategy {
                IdempotencyStrategy::Preserve => {
                    return Err(ToolError::new(
                        "validation_failed",
                        format!("events[{index}].metadata.idempotency_key is required"),
                    )
                    .with_field(format!("events[{index}].metadata.idempotency_key")));
                }
                IdempotencyStrategy::AutoIfMissing => {
                    metadata.insert(
                        "idempotency_key".to_string(),
                        Value::String(Uuid::now_v7().to_string()),
                    );
                }
            },
            Some(_) => {
                return Err(ToolError::new(
                    "validation_failed",
                    format!("events[{index}].metadata.idempotency_key must be a string"),
                )
                .with_field(format!("events[{index}].metadata.idempotency_key")));
            }
        }

        event_obj.insert("metadata".to_string(), Value::Object(metadata));
        out.push(Value::Object(event_obj));
    }

    Ok(out)
}

fn set_default_metadata_string(
    metadata: &mut Map<String, Value>,
    key: &str,
    default: Option<&str>,
    field: &str,
) -> Result<(), ToolError> {
    match metadata.get(key) {
        None | Some(Value::Null) => {
            if let Some(default) = default {
                metadata.insert(key.to_string(), Value::String(default.to_string()));
            }
            Ok(())
        }
        Some(Value::String(v)) if v.trim().is_empty() => {
            if let Some(default) = default {
                metadata.insert(key.to_string(), Value::String(default.to_string()));
            }
            Ok(())
        }
        Some(Value::String(_)) => Ok(()),
        Some(_) => Err(
            ToolError::new("validation_failed", format!("'{field}' must be a string"))
                .with_field(field),
        ),
    }
}

fn parse_read_after_write_targets(value: Option<&Value>) -> Result<Vec<Value>, ToolError> {
    let Some(value) = value else {
        return Err(ToolError::new(
            "validation_failed",
            "read_after_write_targets is required for mode=write_with_proof",
        )
        .with_field("read_after_write_targets"));
    };

    let targets = value.as_array().ok_or_else(|| {
        ToolError::new(
            "validation_failed",
            "read_after_write_targets must be an array",
        )
        .with_field("read_after_write_targets")
    })?;
    if targets.is_empty() {
        return Err(ToolError::new(
            "validation_failed",
            "read_after_write_targets must not be empty",
        )
        .with_field("read_after_write_targets"));
    }

    let mut out = Vec::new();
    let mut dedup = HashSet::new();
    for (index, target) in targets.iter().enumerate() {
        let target = target.as_object().ok_or_else(|| {
            ToolError::new(
                "validation_failed",
                format!("read_after_write_targets[{index}] must be an object"),
            )
            .with_field("read_after_write_targets")
        })?;
        let projection_type = target
            .get("projection_type")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .ok_or_else(|| {
                ToolError::new(
                    "validation_failed",
                    format!("read_after_write_targets[{index}].projection_type is required"),
                )
                .with_field("read_after_write_targets")
            })?
            .to_lowercase();
        let key = target
            .get("key")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .ok_or_else(|| {
                ToolError::new(
                    "validation_failed",
                    format!("read_after_write_targets[{index}].key is required"),
                )
                .with_field("read_after_write_targets")
            })?
            .to_lowercase();

        if dedup.insert((projection_type.clone(), key.clone())) {
            out.push(json!({
                "projection_type": projection_type,
                "key": key
            }));
        }
    }

    Ok(out)
}

fn pairs_to_json_object(pairs: &[(String, String)]) -> Value {
    let mut map = Map::new();
    for (k, v) in pairs {
        map.insert(k.clone(), Value::String(v.clone()));
    }
    Value::Object(map)
}

fn parse_response_body(bytes: &[u8]) -> Value {
    if bytes.is_empty() {
        return Value::Null;
    }
    serde_json::from_slice(bytes)
        .unwrap_or_else(|_| Value::String(String::from_utf8_lossy(bytes).to_string()))
}

fn extract_openapi_endpoints(spec: &Value) -> Vec<Value> {
    let mut endpoints = Vec::new();
    let Some(paths) = spec.get("paths").and_then(Value::as_object) else {
        return endpoints;
    };

    for (path, methods) in paths {
        let Some(methods) = methods.as_object() else {
            continue;
        };
        for (method, details) in methods {
            if !matches!(
                method.as_str(),
                "get" | "post" | "put" | "patch" | "delete" | "head" | "options"
            ) {
                continue;
            }
            let summary = details
                .get("summary")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let requires_auth = details
                .get("security")
                .map(|security| {
                    !security
                        .as_array()
                        .map(|arr| arr.is_empty())
                        .unwrap_or(true)
                })
                .unwrap_or(false);
            endpoints.push(json!({
                "method": method.to_uppercase(),
                "path": path,
                "summary": summary,
                "auth": requires_auth
            }));
        }
    }

    endpoints.sort_by(|a, b| {
        let path_a = a.get("path").and_then(Value::as_str).unwrap_or_default();
        let path_b = b.get("path").and_then(Value::as_str).unwrap_or_default();
        let method_a = a.get("method").and_then(Value::as_str).unwrap_or_default();
        let method_b = b.get("method").and_then(Value::as_str).unwrap_or_default();
        path_a.cmp(path_b).then(method_a.cmp(method_b))
    });
    endpoints
}

fn success_response(id: Value, result: Value) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "result": result
    })
}

fn error_response(id: Value, error: RpcError) -> Value {
    let mut payload = json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": error.code,
            "message": error.message
        }
    });
    if let Some(data) = error.data {
        payload["error"]["data"] = data;
    }
    payload
}

async fn read_framed_json(
    reader: &mut BufReader<tokio::io::Stdin>,
) -> Result<Option<Value>, std::io::Error> {
    let mut content_length: Option<usize> = None;

    loop {
        let mut line = String::new();
        let bytes_read = reader.read_line(&mut line).await?;
        if bytes_read == 0 {
            if content_length.is_none() {
                return Ok(None);
            }
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "Unexpected EOF while reading MCP headers",
            ));
        }

        if line == "\r\n" {
            break;
        }

        let line = line.trim_end_matches(['\r', '\n']);
        if line.to_ascii_lowercase().starts_with("content-length:") {
            let raw_len = line
                .split_once(':')
                .map(|(_, right)| right.trim())
                .unwrap_or_default();
            let parsed = raw_len.parse::<usize>().map_err(|_| {
                std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    "Invalid Content-Length header",
                )
            })?;
            content_length = Some(parsed);
        }
    }

    let content_length = content_length.ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "Missing Content-Length header",
        )
    })?;
    let mut payload = vec![0_u8; content_length];
    reader.read_exact(&mut payload).await?;

    let json: Value = serde_json::from_slice(&payload).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("Invalid JSON payload: {e}"),
        )
    })?;
    Ok(Some(json))
}

async fn write_framed_json(
    writer: &mut tokio::io::Stdout,
    value: &Value,
) -> Result<(), std::io::Error> {
    let body = serde_json::to_vec(value).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("Failed to serialize JSON: {e}"),
        )
    })?;
    let header = format!(
        "Content-Length: {}\r\nContent-Type: application/json\r\n\r\n",
        body.len()
    );
    writer.write_all(header.as_bytes()).await?;
    writer.write_all(&body).await?;
    writer.flush().await?;
    Ok(())
}

fn to_pretty_json(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| "{}".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn normalize_api_path_adds_leading_slash() {
        assert_eq!(normalize_api_path("v1/events").unwrap(), "/v1/events");
        assert_eq!(normalize_api_path("/v1/events").unwrap(), "/v1/events");
    }

    #[test]
    fn ensure_event_defaults_fills_metadata_and_timestamp() {
        let defaults = MetadataDefaults {
            source: Some("mcp".to_string()),
            agent: Some("kura-mcp".to_string()),
            device: None,
            session_id: None,
        };
        let events = vec![json!({
            "event_type": "set.logged",
            "data": { "reps": 5, "weight_kg": 100 }
        })];
        let normalized =
            ensure_event_defaults(&events, &defaults, IdempotencyStrategy::AutoIfMissing).unwrap();
        let event = normalized[0].as_object().unwrap();
        assert!(event.get("timestamp").and_then(Value::as_str).is_some());
        let metadata = event.get("metadata").and_then(Value::as_object).unwrap();
        assert_eq!(metadata.get("source").and_then(Value::as_str), Some("mcp"));
        assert_eq!(
            metadata.get("agent").and_then(Value::as_str),
            Some("kura-mcp")
        );
        let idempotency_key = metadata
            .get("idempotency_key")
            .and_then(Value::as_str)
            .unwrap();
        assert!(!idempotency_key.is_empty());
    }

    #[test]
    fn preserve_idempotency_strategy_requires_key() {
        let defaults = MetadataDefaults::default();
        let events = vec![json!({
            "event_type": "set.logged",
            "data": { "reps": 3 }
        })];
        let err = ensure_event_defaults(&events, &defaults, IdempotencyStrategy::Preserve)
            .expect_err("expected missing idempotency_key error");
        assert_eq!(err.code, "validation_failed");
    }

    #[test]
    fn parse_query_pairs_accepts_object_and_array() {
        let from_object =
            parse_query_pairs(Some(&json!({"event_type": "set.logged", "limit": 10}))).unwrap();
        assert_eq!(from_object.len(), 2);

        let from_array = parse_query_pairs(Some(&json!([
            {"key": "event_type", "value": "set.logged"},
            {"key": "limit", "value": 10}
        ])))
        .unwrap();
        assert_eq!(from_array.len(), 2);
    }

    #[test]
    fn extract_openapi_endpoints_sorts_and_compacts() {
        let spec = json!({
            "paths": {
                "/v1/events": {
                    "post": { "summary": "Create event", "security": [{"bearer_auth": []}] },
                    "get": { "summary": "List events", "security": [{"bearer_auth": []}] }
                },
                "/health": {
                    "get": { "summary": "Health check" }
                }
            }
        });
        let endpoints = extract_openapi_endpoints(&spec);
        assert_eq!(endpoints.len(), 3);
        assert_eq!(endpoints[0]["path"], "/health");
        assert_eq!(endpoints[1]["method"], "GET");
        assert_eq!(endpoints[2]["method"], "POST");
    }
}
