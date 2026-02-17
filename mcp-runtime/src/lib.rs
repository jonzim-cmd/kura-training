use std::collections::{BTreeMap, HashSet};

use clap::{Args, Subcommand};
use reqwest::Method;
use serde_json::{Map, Value, json};
use tokio::io::{self, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use uuid::Uuid;

mod util;

use util::{client, resolve_token};

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
    /// Allow admin API paths (disabled by default)
    #[arg(long, env = "KURA_MCP_ALLOW_ADMIN")]
    pub allow_admin: bool,
}

pub async fn run(api_url: &str, inherited_no_auth: bool, command: McpCommands) -> i32 {
    match command {
        McpCommands::Serve(args) => {
            let mut server = McpServer::new(McpRuntimeConfig {
                api_url: api_url.to_string(),
                no_auth: inherited_no_auth || args.no_auth,
                explicit_token: args.token,
                default_source: args.default_source,
                default_agent: args.default_agent,
                allow_admin: args.allow_admin,
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
pub struct HttpMcpRequestConfig {
    pub no_auth: bool,
    pub token: Option<String>,
    pub default_source: String,
    pub default_agent: String,
    pub allow_admin: bool,
}

impl Default for HttpMcpRequestConfig {
    fn default() -> Self {
        Self {
            no_auth: false,
            token: None,
            default_source: "mcp".to_string(),
            default_agent: "kura-mcp".to_string(),
            allow_admin: false,
        }
    }
}

pub async fn handle_http_jsonrpc(
    api_url: &str,
    config: HttpMcpRequestConfig,
    incoming: Value,
) -> Vec<Value> {
    let mut server = McpServer::new(McpRuntimeConfig {
        api_url: api_url.to_string(),
        no_auth: config.no_auth,
        explicit_token: config.token,
        default_source: config.default_source,
        default_agent: config.default_agent,
        allow_admin: config.allow_admin,
    });
    server.capability_profile = server.negotiate_capability_profile().await;
    server.handle_incoming_message(incoming).await
}

#[derive(Clone, Debug)]
struct McpRuntimeConfig {
    api_url: String,
    no_auth: bool,
    explicit_token: Option<String>,
    default_source: String,
    default_agent: String,
    allow_admin: bool,
}

struct McpServer {
    config: McpRuntimeConfig,
    http: reqwest::Client,
    capability_profile: CapabilityProfile,
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum CapabilityMode {
    PreferredContract,
    LegacyFallback,
}

impl CapabilityMode {
    fn as_str(&self) -> &'static str {
        match self {
            CapabilityMode::PreferredContract => "preferred_contract",
            CapabilityMode::LegacyFallback => "legacy_fallback",
        }
    }
}

#[derive(Clone, Debug)]
struct CapabilityProfile {
    mode: CapabilityMode,
    negotiated_at: chrono::DateTime<chrono::Utc>,
    reason: String,
    preferred_read_endpoint: String,
    preferred_write_endpoint: String,
    legacy_read_endpoint: String,
    legacy_single_write_endpoint: String,
    legacy_batch_write_endpoint: String,
    write_with_proof_supported: bool,
    manifest_snapshot: Option<Value>,
    warnings: Vec<String>,
}

impl CapabilityProfile {
    fn preferred(
        read: String,
        write: String,
        manifest_snapshot: Value,
        warnings: Vec<String>,
    ) -> Self {
        Self {
            mode: CapabilityMode::PreferredContract,
            negotiated_at: chrono::Utc::now(),
            reason: "capabilities_manifest_ok".to_string(),
            preferred_read_endpoint: read,
            preferred_write_endpoint: write,
            legacy_read_endpoint: "/v1/projections".to_string(),
            legacy_single_write_endpoint: "/v1/events".to_string(),
            legacy_batch_write_endpoint: "/v1/events/batch".to_string(),
            write_with_proof_supported: true,
            manifest_snapshot: Some(manifest_snapshot),
            warnings,
        }
    }

    fn fallback(
        reason: impl Into<String>,
        warnings: Vec<String>,
        manifest_snapshot: Option<Value>,
    ) -> Self {
        let reason = reason.into();
        Self {
            mode: CapabilityMode::LegacyFallback,
            negotiated_at: chrono::Utc::now(),
            reason,
            preferred_read_endpoint: "/v1/agent/context".to_string(),
            preferred_write_endpoint: "/v1/agent/write-with-proof".to_string(),
            legacy_read_endpoint: "/v1/projections".to_string(),
            legacy_single_write_endpoint: "/v1/events".to_string(),
            legacy_batch_write_endpoint: "/v1/events/batch".to_string(),
            write_with_proof_supported: false,
            manifest_snapshot,
            warnings,
        }
    }

    fn effective_read_endpoint(&self) -> &str {
        if self.mode == CapabilityMode::PreferredContract {
            &self.preferred_read_endpoint
        } else {
            &self.legacy_read_endpoint
        }
    }

    fn supports_write_with_proof(&self) -> bool {
        self.mode == CapabilityMode::PreferredContract && self.write_with_proof_supported
    }

    fn to_value(&self) -> Value {
        let mut payload = json!({
            "mode": self.mode.as_str(),
            "reason": self.reason,
            "negotiated_at": self.negotiated_at,
            "preferred_read_endpoint": self.preferred_read_endpoint,
            "preferred_write_endpoint": self.preferred_write_endpoint,
            "legacy_read_endpoint": self.legacy_read_endpoint,
            "legacy_single_write_endpoint": self.legacy_single_write_endpoint,
            "legacy_batch_write_endpoint": self.legacy_batch_write_endpoint,
            "write_with_proof_supported": self.supports_write_with_proof(),
        });
        if !self.warnings.is_empty() {
            payload["warnings"] =
                Value::Array(self.warnings.iter().cloned().map(Value::String).collect());
        }
        if let Some(manifest_snapshot) = &self.manifest_snapshot {
            payload["manifest_snapshot"] = manifest_snapshot.clone();
        }
        payload
    }
}

impl McpServer {
    fn new(config: McpRuntimeConfig) -> Self {
        Self {
            config,
            http: client(),
            capability_profile: CapabilityProfile::fallback("not_negotiated_yet", Vec::new(), None),
        }
    }

    async fn serve_stdio(&mut self) -> Result<(), String> {
        self.capability_profile = self.negotiate_capability_profile().await;
        self.emit_capability_status();

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

    fn emit_capability_status(&self) {
        let payload = json!({
            "event": "mcp_capability_negotiation",
            "server": MCP_SERVER_NAME,
            "version": env!("CARGO_PKG_VERSION"),
            "profile": self.capability_profile.to_value(),
        });
        eprintln!("{}", to_pretty_json(&payload));
    }

    async fn negotiate_capability_profile(&self) -> CapabilityProfile {
        let result = self
            .send_api_request(
                Method::GET,
                "/v1/agent/capabilities",
                &[],
                None,
                true,
                false,
            )
            .await;
        capability_profile_from_negotiation(result)
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
        let instructions = format!(
            "Start with kura_agent_context (context-first). If user_profile agenda includes onboarding_needed, reply first with: (1) what Kura is (use first_contact_opening_v1 mandatory sentence), (2) how to use it briefly, (3) propose a short onboarding interview before feature menus or logging steps. Use kura_discover only for schema/capability troubleshooting, and prefer kura_events_write with mode=simulate before commit for higher confidence. Capability mode: {}.",
            self.capability_profile.mode.as_str()
        );
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
            "instructions": instructions,
            "capabilityStatus": self.capability_profile.to_value()
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
            Ok(payload) => {
                let status = tool_completion_status(&payload);
                let envelope = json!({
                    "status": status,
                    "phase": "final",
                    "tool": name,
                    "data": payload
                });
                json!({
                    "content": [{ "type": "text", "text": to_pretty_json(&envelope) }],
                    "structuredContent": envelope
                })
            }
            Err(err) => {
                let payload = err.to_value();
                let envelope = json!({
                    "status": "error",
                    "phase": "final",
                    "tool": name,
                    "error": payload
                });
                json!({
                    "isError": true,
                    "content": [{ "type": "text", "text": to_pretty_json(&envelope) }],
                    "structuredContent": envelope
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
            "kura_mcp_status" => self.tool_mcp_status(args).await,
            "kura_api_request" => self.tool_api_request(args).await,
            "kura_events_write" => self.tool_events_write(args).await,
            "kura_events_list" => self.tool_events_list(args).await,
            "kura_projection_get" => self.tool_projection_get(args).await,
            "kura_projection_list" => self.tool_projection_list(args).await,
            "kura_agent_context" => self.tool_agent_context(args).await,
            "kura_semantic_resolve" => self.tool_semantic_resolve(args).await,
            "kura_access_request" => self.tool_access_request(args).await,
            "kura_account_api_keys_list" => self.tool_account_api_keys_list(args).await,
            "kura_account_api_keys_create" => self.tool_account_api_keys_create(args).await,
            "kura_account_api_keys_revoke" => self.tool_account_api_keys_revoke(args).await,
            "kura_import_job_create" => self.tool_import_job_create(args).await,
            "kura_import_job_get" => self.tool_import_job_get(args).await,
            "kura_provider_connections_list" => self.tool_provider_connections_list(args).await,
            "kura_provider_connections_upsert" => self.tool_provider_connections_upsert(args).await,
            "kura_provider_connection_revoke" => self.tool_provider_connection_revoke(args).await,
            "kura_agent_visualization_resolve" => self.tool_agent_visualization_resolve(args).await,
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
        payload["mcp_capability_status"] = self.capability_profile.to_value();

        Ok(payload)
    }

    async fn tool_mcp_status(&self, _args: &Map<String, Value>) -> Result<Value, ToolError> {
        Ok(json!({
            "server": {
                "name": MCP_SERVER_NAME,
                "version": env!("CARGO_PKG_VERSION"),
                "protocol_version": MCP_PROTOCOL_VERSION
            },
            "capability_negotiation": self.capability_profile.to_value()
        }))
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
        let mut compatibility_notes = Vec::<String>::new();
        let mut fallback_applied = false;
        let mut requested_path: Option<String> = None;
        let mut effective_mode = mode.as_str().to_string();

        let (path, body) = match mode {
            WriteMode::Commit => legacy_write_target(
                &normalized_events,
                &self.capability_profile.legacy_single_write_endpoint,
                &self.capability_profile.legacy_batch_write_endpoint,
            ),
            WriteMode::Simulate => (
                "/v1/events/simulate".to_string(),
                json!({ "events": normalized_events }),
            ),
            WriteMode::WriteWithProof => {
                if self.capability_profile.supports_write_with_proof() {
                    let targets =
                        parse_read_after_write_targets(args.get("read_after_write_targets"))?;
                    let mut body = json!({
                        "events": normalized_events,
                        "read_after_write_targets": targets
                    });
                    if let Some(verify_timeout_ms) = arg_optional_u64(args, "verify_timeout_ms")? {
                        body["verify_timeout_ms"] = json!(verify_timeout_ms);
                    }
                    if has_high_impact_events(&normalized_events) {
                        let handshake = match args.get("intent_handshake") {
                            Some(Value::Object(raw_handshake)) => {
                                let mut handshake = Value::Object(raw_handshake.clone());
                                if handshake
                                    .as_object()
                                    .and_then(|obj| obj.get("temporal_basis"))
                                    .is_none()
                                {
                                    let temporal_basis = self
                                        .resolve_temporal_basis_for_high_impact_write(args)
                                        .await?;
                                    handshake["temporal_basis"] = temporal_basis;
                                }
                                handshake
                            }
                            Some(_) => {
                                return Err(ToolError::new(
                                    "validation_failed",
                                    "intent_handshake must be an object when provided",
                                )
                                .with_field("intent_handshake"));
                            }
                            None => {
                                let goal = arg_optional_string(args, "intent_goal")?;
                                let temporal_basis = self
                                    .resolve_temporal_basis_for_high_impact_write(args)
                                    .await?;
                                build_default_intent_handshake(
                                    &normalized_events,
                                    goal.as_deref(),
                                    temporal_basis,
                                )
                            }
                        };
                        body["intent_handshake"] = handshake;
                    }
                    requested_path = Some(self.capability_profile.preferred_write_endpoint.clone());
                    (
                        self.capability_profile.preferred_write_endpoint.clone(),
                        body,
                    )
                } else {
                    fallback_applied = true;
                    effective_mode = "write_with_proof_fallback_commit".to_string();
                    compatibility_notes.push(
                        "write_with_proof is unavailable in legacy compatibility mode; routing to classic event write endpoints.".to_string(),
                    );
                    legacy_write_target(
                        &normalized_events,
                        &self.capability_profile.legacy_single_write_endpoint,
                        &self.capability_profile.legacy_batch_write_endpoint,
                    )
                }
            }
        };

        let mut response = self
            .send_api_request(Method::POST, &path, &[], Some(body.clone()), true, false)
            .await?;
        let mut effective_path = path.clone();

        if mode == WriteMode::WriteWithProof
            && requested_path.is_some()
            && should_apply_contract_fallback(response.status)
        {
            let unsupported_status = response.status;
            let (legacy_path, legacy_body) = legacy_write_target(
                &normalized_events,
                &self.capability_profile.legacy_single_write_endpoint,
                &self.capability_profile.legacy_batch_write_endpoint,
            );
            response = self
                .send_api_request(
                    Method::POST,
                    &legacy_path,
                    &[],
                    Some(legacy_body),
                    true,
                    false,
                )
                .await?;
            fallback_applied = true;
            effective_mode = "write_with_proof_fallback_commit".to_string();
            compatibility_notes.push(format!(
                "Preferred write-with-proof endpoint returned unsupported status {}; routed to {}.",
                unsupported_status, legacy_path
            ));
            effective_path = legacy_path;
        }

        let contract = write_contract_surface(&response.body);
        if !response.is_success() {
            return Err(ToolError::new(
                "api_error",
                format!("kura_events_write failed with HTTP {}", response.status),
            )
            .with_details(json!({
                "request": {
                    "mode": mode.as_str(),
                    "effective_mode": effective_mode,
                    "path": effective_path,
                    "event_count": events.len()
                },
                "response": response.to_value(),
                "contract": contract,
                "compatibility": {
                    "capability_mode": self.capability_profile.mode.as_str(),
                    "fallback_applied": fallback_applied,
                    "notes": compatibility_notes
                }
            })));
        }

        let persist_intent_mode = contract
            .get("persist_intent")
            .and_then(|v| v.get("mode"))
            .cloned()
            .unwrap_or(Value::Null);
        let persist_status_label = contract
            .get("persist_intent")
            .and_then(|v| v.get("status_label"))
            .cloned()
            .unwrap_or(Value::Null);

        Ok(json!({
            "request": {
                "mode": mode.as_str(),
                "effective_mode": effective_mode,
                "path": effective_path,
                "event_count": events.len()
            },
            "response": response.to_value(),
            "contract": contract,
            "completion": {
                "status": if fallback_applied { "complete_with_fallback" } else { "complete" },
                "event_count": events.len(),
                "verification_contract_enforced": mode != WriteMode::WriteWithProof || !fallback_applied,
                "persist_intent_mode": persist_intent_mode,
                "persist_status_label": persist_status_label
            },
            "compatibility": {
                "capability_mode": self.capability_profile.mode.as_str(),
                "fallback_applied": fallback_applied,
                "notes": compatibility_notes
            }
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
        let mut compatibility_notes = Vec::<String>::new();
        let preferred_path = self
            .capability_profile
            .effective_read_endpoint()
            .to_string();
        let mut effective_query = if self.capability_profile.mode
            == CapabilityMode::PreferredContract
        {
            query.clone()
        } else {
            compatibility_notes.push(
                "Agent context contract unavailable; using legacy /v1/projections snapshot semantics."
                    .to_string(),
            );
            Vec::new()
        };
        let mut response = self
            .send_api_request(
                Method::GET,
                &preferred_path,
                &effective_query,
                None,
                true,
                false,
            )
            .await?;
        let mut effective_path = preferred_path.clone();
        let mut fallback_applied = false;

        if self.capability_profile.mode == CapabilityMode::PreferredContract
            && should_apply_contract_fallback(response.status)
        {
            let unsupported_status = response.status;
            fallback_applied = true;
            effective_path = self.capability_profile.legacy_read_endpoint.clone();
            effective_query.clear();
            compatibility_notes.push(format!(
                "Preferred context endpoint returned unsupported status {}; routed to legacy {}.",
                unsupported_status, effective_path
            ));
            response = self
                .send_api_request(
                    Method::GET,
                    &effective_path,
                    &effective_query,
                    None,
                    true,
                    false,
                )
                .await?;
        }

        Ok(json!({
            "request": {
                "path": effective_path,
                "query": pairs_to_json_object(&effective_query)
            },
            "response": response.to_value(),
            "completion": {
                "status": if fallback_applied { "complete_with_fallback" } else { "complete" }
            },
            "compatibility": {
                "capability_mode": self.capability_profile.mode.as_str(),
                "fallback_applied": fallback_applied,
                "notes": compatibility_notes
            }
        }))
    }

    async fn resolve_temporal_basis_for_high_impact_write(
        &self,
        args: &Map<String, Value>,
    ) -> Result<Value, ToolError> {
        if let Some(raw_basis) = args.get("temporal_basis") {
            if !raw_basis.is_object() {
                return Err(ToolError::new(
                    "validation_failed",
                    "temporal_basis must be an object when provided",
                )
                .with_field("temporal_basis"));
            }
            return Ok(raw_basis.clone());
        }

        if self.capability_profile.mode != CapabilityMode::PreferredContract {
            return Err(
                ToolError::new(
                    "validation_failed",
                    "temporal_basis is required for high-impact write_with_proof in legacy fallback mode",
                )
                .with_field("temporal_basis")
                .with_docs_hint(
                    "Provide temporal_basis explicitly from a recent /v1/agent/context response.",
                ),
            );
        }

        let response = self
            .send_api_request(
                Method::GET,
                self.capability_profile.effective_read_endpoint(),
                &[],
                None,
                true,
                false,
            )
            .await?;
        if response.status >= 400 {
            return Err(ToolError::new(
                "temporal_context_unavailable",
                format!(
                    "Failed to fetch fresh agent context for temporal_basis (HTTP {})",
                    response.status
                ),
            )
            .with_field("temporal_basis")
            .with_docs_hint(
                "Retry after GET /v1/agent/context succeeds, or provide temporal_basis explicitly.",
            ));
        }

        let temporal_context = response
            .body
            .get("meta")
            .and_then(|meta| meta.get("temporal_context"))
            .and_then(Value::as_object)
            .ok_or_else(|| {
                ToolError::new(
                    "temporal_context_missing",
                    "Agent context response does not contain meta.temporal_context",
                )
                .with_field("temporal_basis")
                .with_docs_hint(
                    "Upgrade API/runtime to agent_context temporal grounding contract before high-impact writes.",
                )
            })?;

        let context_generated_at = temporal_context.get("now_utc").cloned().ok_or_else(|| {
            ToolError::new(
                "temporal_context_missing",
                "meta.temporal_context.now_utc is required",
            )
            .with_field("temporal_basis.context_generated_at")
        })?;
        let timezone = temporal_context.get("timezone").cloned().ok_or_else(|| {
            ToolError::new(
                "temporal_context_missing",
                "meta.temporal_context.timezone is required",
            )
            .with_field("temporal_basis.timezone")
        })?;
        let today_local_date = temporal_context
            .get("today_local_date")
            .cloned()
            .ok_or_else(|| {
                ToolError::new(
                    "temporal_context_missing",
                    "meta.temporal_context.today_local_date is required",
                )
                .with_field("temporal_basis.today_local_date")
            })?;

        let mut temporal_basis = json!({
            "schema_version": "temporal_basis.v1",
            "context_generated_at": context_generated_at,
            "timezone": timezone,
            "today_local_date": today_local_date
        });
        if let Some(value) = temporal_context.get("last_training_date_local") {
            if !value.is_null() {
                temporal_basis["last_training_date_local"] = value.clone();
            }
        }
        if let Some(value) = temporal_context.get("days_since_last_training") {
            if !value.is_null() {
                temporal_basis["days_since_last_training"] = value.clone();
            }
        }

        Ok(temporal_basis)
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

    async fn tool_access_request(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let email = required_string(args, "email")?;
        let mut body = json!({ "email": email });
        if let Some(name) = arg_optional_string(args, "name")? {
            body["name"] = json!(name);
        }
        if let Some(context) = arg_optional_string(args, "context")? {
            body["context"] = json!(context);
        }
        if let Some(locale) = arg_optional_string(args, "locale")? {
            body["locale"] = json!(locale);
        }
        if let Some(turnstile_token) = arg_optional_string(args, "turnstile_token")? {
            body["turnstile_token"] = json!(turnstile_token);
        }

        let response = self
            .send_api_request(
                Method::POST,
                "/v1/access/request",
                &[],
                Some(body),
                false,
                false,
            )
            .await?;

        Ok(json!({
            "request": { "path": "/v1/access/request" },
            "response": response.to_value()
        }))
    }

    async fn tool_account_api_keys_list(
        &self,
        _args: &Map<String, Value>,
    ) -> Result<Value, ToolError> {
        let response = self
            .send_api_request(Method::GET, "/v1/account/api-keys", &[], None, true, false)
            .await?;

        Ok(json!({
            "request": { "path": "/v1/account/api-keys" },
            "response": response.to_value()
        }))
    }

    async fn tool_account_api_keys_create(
        &self,
        args: &Map<String, Value>,
    ) -> Result<Value, ToolError> {
        let label = required_string(args, "label")?;
        let mut body = json!({ "label": label });
        if let Some(scopes) = arg_optional_string_array(args, "scopes")? {
            body["scopes"] = json!(scopes);
        }

        let response = self
            .send_api_request(
                Method::POST,
                "/v1/account/api-keys",
                &[],
                Some(body),
                true,
                false,
            )
            .await?;

        Ok(json!({
            "request": { "path": "/v1/account/api-keys" },
            "response": response.to_value()
        }))
    }

    async fn tool_account_api_keys_revoke(
        &self,
        args: &Map<String, Value>,
    ) -> Result<Value, ToolError> {
        let key_id = required_string(args, "key_id")?;
        let key_id = parse_uuid_string(&key_id, "key_id")?;
        let path = format!("/v1/account/api-keys/{key_id}");

        let response = self
            .send_api_request(Method::DELETE, &path, &[], None, true, false)
            .await?;

        Ok(json!({
            "request": { "path": path, "key_id": key_id },
            "response": response.to_value()
        }))
    }

    async fn tool_import_job_create(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let provider = required_string(args, "provider")?;
        let provider_user_id = required_string(args, "provider_user_id")?;
        let file_format = required_string(args, "file_format")?;
        let payload_text = required_string(args, "payload_text")?;
        let external_activity_id = required_string(args, "external_activity_id")?;

        let mut body = json!({
            "provider": provider,
            "provider_user_id": provider_user_id,
            "file_format": file_format,
            "payload_text": payload_text,
            "external_activity_id": external_activity_id
        });
        if let Some(external_event_version) = arg_optional_string(args, "external_event_version")? {
            body["external_event_version"] = json!(external_event_version);
        }
        if let Some(raw_payload_ref) = arg_optional_string(args, "raw_payload_ref")? {
            body["raw_payload_ref"] = json!(raw_payload_ref);
        }
        if let Some(ingestion_method) = arg_optional_string(args, "ingestion_method")? {
            body["ingestion_method"] = json!(ingestion_method);
        }

        let response = self
            .send_api_request(
                Method::POST,
                "/v1/imports/jobs",
                &[],
                Some(body),
                true,
                false,
            )
            .await?;

        Ok(json!({
            "request": { "path": "/v1/imports/jobs" },
            "response": response.to_value()
        }))
    }

    async fn tool_import_job_get(&self, args: &Map<String, Value>) -> Result<Value, ToolError> {
        let job_id = required_string(args, "job_id")?;
        let job_id = parse_uuid_string(&job_id, "job_id")?;
        let path = format!("/v1/imports/jobs/{job_id}");

        let response = self
            .send_api_request(Method::GET, &path, &[], None, true, false)
            .await?;

        Ok(json!({
            "request": { "path": path, "job_id": job_id },
            "response": response.to_value()
        }))
    }

    async fn tool_provider_connections_list(
        &self,
        _args: &Map<String, Value>,
    ) -> Result<Value, ToolError> {
        let response = self
            .send_api_request(
                Method::GET,
                "/v1/providers/connections",
                &[],
                None,
                true,
                false,
            )
            .await?;

        Ok(json!({
            "request": { "path": "/v1/providers/connections" },
            "response": response.to_value()
        }))
    }

    async fn tool_provider_connections_upsert(
        &self,
        args: &Map<String, Value>,
    ) -> Result<Value, ToolError> {
        let provider = required_string(args, "provider")?;
        let provider_account_id = required_string(args, "provider_account_id")?;
        let auth_state = required_string(args, "auth_state")?;

        let mut body = json!({
            "provider": provider,
            "provider_account_id": provider_account_id,
            "auth_state": auth_state,
        });
        if let Some(scopes) = arg_optional_string_array(args, "scopes")? {
            body["scopes"] = json!(scopes);
        }
        if let Some(consented_at) = arg_optional_string(args, "consented_at")? {
            body["consented_at"] = json!(consented_at);
        }
        if let Some(token_expires_at) = arg_optional_string(args, "token_expires_at")? {
            body["token_expires_at"] = json!(token_expires_at);
        }
        if let Some(sync_cursor) = arg_optional_string(args, "sync_cursor")? {
            body["sync_cursor"] = json!(sync_cursor);
        }
        if let Some(access_token_ref) = arg_optional_string(args, "access_token_ref")? {
            body["access_token_ref"] = json!(access_token_ref);
        }
        if let Some(refresh_token_ref) = arg_optional_string(args, "refresh_token_ref")? {
            body["refresh_token_ref"] = json!(refresh_token_ref);
        }
        if let Some(token_fingerprint) = arg_optional_string(args, "token_fingerprint")? {
            body["token_fingerprint"] = json!(token_fingerprint);
        }
        if let Some(last_oauth_state_nonce) = arg_optional_string(args, "last_oauth_state_nonce")? {
            body["last_oauth_state_nonce"] = json!(last_oauth_state_nonce);
        }
        if let Some(last_error_code) = arg_optional_string(args, "last_error_code")? {
            body["last_error_code"] = json!(last_error_code);
        }
        if let Some(last_error_at) = arg_optional_string(args, "last_error_at")? {
            body["last_error_at"] = json!(last_error_at);
        }

        let response = self
            .send_api_request(
                Method::POST,
                "/v1/providers/connections",
                &[],
                Some(body),
                true,
                false,
            )
            .await?;

        Ok(json!({
            "request": { "path": "/v1/providers/connections" },
            "response": response.to_value()
        }))
    }

    async fn tool_provider_connection_revoke(
        &self,
        args: &Map<String, Value>,
    ) -> Result<Value, ToolError> {
        let connection_id = required_string(args, "connection_id")?;
        let connection_id = parse_uuid_string(&connection_id, "connection_id")?;
        let reason = required_string(args, "reason")?;
        let path = format!("/v1/providers/connections/{connection_id}/revoke");
        let body = json!({ "reason": reason });

        let response = self
            .send_api_request(Method::POST, &path, &[], Some(body), true, false)
            .await?;

        Ok(json!({
            "request": { "path": path, "connection_id": connection_id },
            "response": response.to_value()
        }))
    }

    async fn tool_agent_visualization_resolve(
        &self,
        args: &Map<String, Value>,
    ) -> Result<Value, ToolError> {
        let task_intent = required_string(args, "task_intent")?;
        let mut body = json!({
            "task_intent": task_intent,
            "allow_rich_rendering": arg_bool(args, "allow_rich_rendering", true)?
        });
        if let Some(value) = arg_optional_string(args, "user_preference_override")? {
            body["user_preference_override"] = json!(value);
        }
        if let Some(value) = arg_optional_string(args, "complexity_hint")? {
            body["complexity_hint"] = json!(value);
        }
        if let Some(value) = arg_optional_string(args, "telemetry_session_id")? {
            body["telemetry_session_id"] = json!(value);
        }
        if let Some(spec) = args.get("visualization_spec") {
            if !spec.is_object() {
                return Err(ToolError::new(
                    "validation_failed",
                    "visualization_spec must be an object when provided",
                )
                .with_field("visualization_spec"));
            }
            body["visualization_spec"] = spec.clone();
        }

        let response = self
            .send_api_request(
                Method::POST,
                "/v1/agent/visualization/resolve",
                &[],
                Some(body),
                true,
                false,
            )
            .await?;

        Ok(json!({
            "request": { "path": "/v1/agent/visualization/resolve" },
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
            "kura://mcp/capability-status" => self.capability_profile.to_value(),
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
        if is_admin_api_path(&path) && !self.config.allow_admin {
            return Err(
                ToolError::new(
                    "admin_path_blocked",
                    "Admin API paths are disabled in MCP by default",
                )
                .with_field("path")
                .with_docs_hint(
                    "Start MCP with --allow-admin (or set KURA_MCP_ALLOW_ADMIN=1) only in trusted developer/admin sessions.",
                ),
            );
        }
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

    fn with_details(mut self, details: Value) -> Self {
        self.details = Some(details);
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
            name: "kura_mcp_status",
            description: "Show MCP capability negotiation status and active routing mode.",
            input_schema: json!({
                "type": "object",
                "properties": {},
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
                    ,
                    "intent_goal": { "type": "string", "description": "Optional high-level goal used when auto-generating intent_handshake for high-impact write_with_proof calls." },
                    "intent_handshake": {
                        "type": "object",
                        "description": "Optional full intent_handshake.v1 payload. For high-impact write_with_proof calls MCP auto-generates one with temporal_basis when omitted."
                    },
                    "temporal_basis": {
                        "type": "object",
                        "description": "Optional temporal_basis.v1 payload. When omitted for high-impact write_with_proof calls in preferred-contract mode, MCP fetches /v1/agent/context and derives it automatically."
                    }
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
        ToolDefinition {
            name: "kura_access_request",
            description: "Submit a public access request (no auth required).",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "email": { "type": "string" },
                    "name": { "type": "string" },
                    "context": { "type": "string" },
                    "locale": { "type": "string", "enum": ["de", "en", "ja"] },
                    "turnstile_token": { "type": "string" }
                },
                "required": ["email"],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_account_api_keys_list",
            description: "List API keys for the authenticated account.",
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_account_api_keys_create",
            description: "Create an API key for the authenticated account.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "label": { "type": "string" },
                    "scopes": {
                        "type": "array",
                        "items": { "type": "string" }
                    }
                },
                "required": ["label"],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_account_api_keys_revoke",
            description: "Revoke an API key by key_id.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "key_id": { "type": "string", "description": "UUID" }
                },
                "required": ["key_id"],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_import_job_create",
            description: "Queue a new external import job.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "provider": { "type": "string" },
                    "provider_user_id": { "type": "string" },
                    "file_format": { "type": "string" },
                    "payload_text": { "type": "string" },
                    "external_activity_id": { "type": "string" },
                    "external_event_version": { "type": "string" },
                    "raw_payload_ref": { "type": "string" },
                    "ingestion_method": { "type": "string" }
                },
                "required": [
                    "provider",
                    "provider_user_id",
                    "file_format",
                    "payload_text",
                    "external_activity_id"
                ],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_import_job_get",
            description: "Get status of an import job by job_id.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "job_id": { "type": "string", "description": "UUID" }
                },
                "required": ["job_id"],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_provider_connections_list",
            description: "List provider connections for the authenticated account.",
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_provider_connections_upsert",
            description: "Upsert provider connection metadata.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "provider": { "type": "string" },
                    "provider_account_id": { "type": "string" },
                    "auth_state": { "type": "string" },
                    "scopes": {
                        "type": "array",
                        "items": { "type": "string" }
                    },
                    "consented_at": { "type": "string", "description": "RFC3339 timestamp" },
                    "token_expires_at": { "type": "string", "description": "RFC3339 timestamp" },
                    "sync_cursor": { "type": "string" },
                    "access_token_ref": { "type": "string" },
                    "refresh_token_ref": { "type": "string" },
                    "token_fingerprint": { "type": "string" },
                    "last_oauth_state_nonce": { "type": "string" },
                    "last_error_code": { "type": "string" },
                    "last_error_at": { "type": "string", "description": "RFC3339 timestamp" }
                },
                "required": ["provider", "provider_account_id", "auth_state"],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_provider_connection_revoke",
            description: "Revoke one provider connection by connection_id.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "connection_id": { "type": "string", "description": "UUID" },
                    "reason": { "type": "string" }
                },
                "required": ["connection_id", "reason"],
                "additionalProperties": false
            }),
        },
        ToolDefinition {
            name: "kura_agent_visualization_resolve",
            description: "Resolve visualization policy and output for a task intent.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "task_intent": { "type": "string" },
                    "user_preference_override": { "type": "string", "enum": ["auto", "always", "never"] },
                    "complexity_hint": { "type": "string", "enum": ["low", "medium", "high"] },
                    "allow_rich_rendering": { "type": "boolean", "default": true },
                    "visualization_spec": {
                        "type": "object",
                        "description": "AgentVisualizationSpec payload"
                    },
                    "telemetry_session_id": { "type": "string" }
                },
                "required": ["task_intent"],
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
        ResourceDefinition {
            uri: "kura://mcp/capability-status",
            name: "MCP Capability Status",
            description: "Startup negotiation outcome, active routing mode, and fallback reason",
        },
    ]
}

fn tool_completion_status(payload: &Value) -> &'static str {
    if payload
        .get("compatibility")
        .and_then(|v| v.get("fallback_applied"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        "complete_with_fallback"
    } else {
        "complete"
    }
}

fn write_contract_surface(body: &Value) -> Value {
    json!({
        "verification": body.get("verification").cloned().unwrap_or(Value::Null),
        "claim_guard": body.get("claim_guard").cloned().unwrap_or(Value::Null),
        "persist_intent": body.get("persist_intent").cloned().unwrap_or(Value::Null)
    })
}

fn legacy_write_target(
    normalized_events: &[Value],
    single_path: &str,
    batch_path: &str,
) -> (String, Value) {
    if normalized_events.len() == 1 {
        (single_path.to_string(), normalized_events[0].clone())
    } else {
        (
            batch_path.to_string(),
            json!({ "events": normalized_events.to_vec() }),
        )
    }
}

fn should_apply_contract_fallback(status: u16) -> bool {
    matches!(status, 404 | 405 | 406 | 410 | 501)
}

fn capability_profile_from_negotiation(
    result: Result<ApiCallResult, ToolError>,
) -> CapabilityProfile {
    let result = match result {
        Ok(result) => result,
        Err(err) => {
            return CapabilityProfile::fallback(
                "capability_negotiation_request_failed",
                vec![err.message],
                None,
            );
        }
    };

    if !result.is_success() {
        return CapabilityProfile::fallback(
            format!("capability_negotiation_http_{}", result.status),
            vec![format!(
                "GET /v1/agent/capabilities returned status {}. Falling back to legacy endpoints.",
                result.status
            )],
            Some(result.body),
        );
    }

    let Some(read_endpoint) = result
        .body
        .get("preferred_read_endpoint")
        .and_then(Value::as_str)
        .map(str::trim)
    else {
        return CapabilityProfile::fallback(
            "capability_negotiation_invalid_schema",
            vec![
                "Capabilities manifest missing preferred_read_endpoint; using legacy fallback."
                    .to_string(),
            ],
            Some(result.body),
        );
    };
    let Some(write_endpoint) = result
        .body
        .get("preferred_write_endpoint")
        .and_then(Value::as_str)
        .map(str::trim)
    else {
        return CapabilityProfile::fallback(
            "capability_negotiation_invalid_schema",
            vec![
                "Capabilities manifest missing preferred_write_endpoint; using legacy fallback."
                    .to_string(),
            ],
            Some(result.body),
        );
    };

    if !read_endpoint.starts_with('/') || !write_endpoint.starts_with('/') {
        return CapabilityProfile::fallback(
            "capability_negotiation_invalid_endpoints",
            vec![
                "Capabilities manifest contains non-path preferred endpoints; using legacy fallback."
                    .to_string(),
            ],
            Some(result.body),
        );
    }

    let mut warnings = Vec::new();
    if let Some(min_mcp_version) = result
        .body
        .get("min_mcp_version")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|v| !v.is_empty())
    {
        if min_mcp_version != "not_implemented" {
            match is_version_older(env!("CARGO_PKG_VERSION"), min_mcp_version) {
                Some(true) => {
                    return CapabilityProfile::fallback(
                        "capability_negotiation_version_mismatch",
                        vec![format!(
                            "MCP version {} is older than required min_mcp_version {}. Using legacy fallback.",
                            env!("CARGO_PKG_VERSION"),
                            min_mcp_version
                        )],
                        Some(result.body),
                    );
                }
                Some(false) => {}
                None => warnings.push(format!(
                    "Could not parse min_mcp_version '{}' as semver. Continuing with preferred contract.",
                    min_mcp_version
                )),
            }
        }
    }

    CapabilityProfile::preferred(
        read_endpoint.to_string(),
        write_endpoint.to_string(),
        result.body,
        warnings,
    )
}

fn is_version_older(current: &str, minimum: &str) -> Option<bool> {
    let current = parse_semver_triplet(current)?;
    let minimum = parse_semver_triplet(minimum)?;
    Some(current < minimum)
}

fn parse_semver_triplet(raw: &str) -> Option<(u64, u64, u64)> {
    let clean = raw.trim().trim_start_matches('v');
    let base = clean.split_once('-').map(|(base, _)| base).unwrap_or(clean);
    let mut parts = base.split('.');
    let major = parts.next()?.parse::<u64>().ok()?;
    let minor = parts.next()?.parse::<u64>().ok()?;
    let patch = parts.next()?.parse::<u64>().ok()?;
    Some((major, minor, patch))
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

fn is_admin_api_path(path: &str) -> bool {
    let p = path.trim().to_lowercase();
    p == "/v1/admin" || p.starts_with("/v1/admin/")
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

fn arg_optional_string_array(
    args: &Map<String, Value>,
    key: &str,
) -> Result<Option<Vec<String>>, ToolError> {
    let Some(value) = args.get(key) else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    let items = value.as_array().ok_or_else(|| {
        ToolError::new(
            "validation_failed",
            format!("'{key}' must be an array of strings"),
        )
        .with_field(key)
    })?;
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        let text = item.as_str().ok_or_else(|| {
            ToolError::new(
                "validation_failed",
                format!("'{key}' items must be strings"),
            )
            .with_field(key)
        })?;
        let normalized = text.trim();
        if !normalized.is_empty() {
            out.push(normalized.to_string());
        }
    }
    Ok(Some(out))
}

fn parse_uuid_string(value: &str, field: &str) -> Result<Uuid, ToolError> {
    Uuid::parse_str(value).map_err(|_| {
        ToolError::new(
            "validation_failed",
            format!("'{field}' must be a valid UUID"),
        )
        .with_field(field)
    })
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

fn is_high_impact_event_type(event_type: &str) -> bool {
    matches!(
        event_type.trim().to_lowercase().as_str(),
        "training_plan.created"
            | "training_plan.updated"
            | "training_plan.archived"
            | "projection_rule.created"
            | "projection_rule.archived"
            | "weight_target.set"
            | "sleep_target.set"
            | "nutrition_target.set"
            | "workflow.onboarding.closed"
            | "workflow.onboarding.override_granted"
    )
}

fn has_high_impact_events(events: &[Value]) -> bool {
    events.iter().any(|event| {
        event
            .as_object()
            .and_then(|obj| obj.get("event_type"))
            .and_then(Value::as_str)
            .is_some_and(is_high_impact_event_type)
    })
}

fn build_default_intent_handshake(
    events: &[Value],
    intent_goal: Option<&str>,
    temporal_basis: Value,
) -> Value {
    let event_types: Vec<String> = events
        .iter()
        .filter_map(|event| {
            event
                .as_object()
                .and_then(|obj| obj.get("event_type"))
                .and_then(Value::as_str)
                .map(|value| value.trim().to_lowercase())
        })
        .collect();
    let planned_action = if event_types.is_empty() {
        "apply high-impact write-with-proof update".to_string()
    } else {
        format!("write events: {}", event_types.join(", "))
    };

    json!({
        "schema_version": "intent_handshake.v1",
        "goal": intent_goal.unwrap_or("execute requested high-impact write safely"),
        "planned_action": planned_action,
        "assumptions": ["context and request intent are current"],
        "non_goals": ["no unrelated writes outside current task scope"],
        "impact_class": "high_impact_write",
        "success_criteria": "write_with_proof returns verification and claim_guard for this action",
        "created_at": chrono::Utc::now().to_rfc3339(),
        "handshake_id": format!("mcp-hs-{}", Uuid::now_v7()),
        "temporal_basis": temporal_basis,
    })
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
    use serde_json::{Value, json};

    #[test]
    fn normalize_api_path_adds_leading_slash() {
        assert_eq!(normalize_api_path("v1/events").unwrap(), "/v1/events");
        assert_eq!(normalize_api_path("/v1/events").unwrap(), "/v1/events");
    }

    #[test]
    fn admin_api_path_detection_is_strict_to_v1_admin_namespace() {
        assert!(is_admin_api_path("/v1/admin"));
        assert!(is_admin_api_path("/v1/admin/security/kill-switch"));
        assert!(!is_admin_api_path("/v1/agent/context"));
        assert!(!is_admin_api_path("/health"));
    }

    #[test]
    fn initialize_instructions_prioritize_context_and_first_contact_onboarding() {
        let server = McpServer::new(McpRuntimeConfig {
            api_url: "http://127.0.0.1:9".to_string(),
            no_auth: true,
            explicit_token: None,
            default_source: "mcp".to_string(),
            default_agent: "kura-mcp".to_string(),
            allow_admin: false,
        });

        let payload = server.initialize_payload();
        let instructions = payload
            .get("instructions")
            .and_then(Value::as_str)
            .expect("initialize payload should include instructions");

        assert!(instructions.contains("kura_agent_context"));
        assert!(instructions.contains("onboarding_needed"));
        assert!(instructions.contains("first_contact_opening_v1"));
        assert!(instructions.contains("kura_discover only for schema/capability troubleshooting"));
    }

    #[tokio::test]
    async fn send_api_request_blocks_admin_paths_when_admin_not_allowed() {
        let server = McpServer::new(McpRuntimeConfig {
            api_url: "http://127.0.0.1:9".to_string(),
            no_auth: true,
            explicit_token: None,
            default_source: "mcp".to_string(),
            default_agent: "kura-mcp".to_string(),
            allow_admin: false,
        });

        let err = server
            .send_api_request(
                Method::GET,
                "/v1/admin/security/kill-switch",
                &[],
                None,
                false,
                false,
            )
            .await
            .expect_err("admin path should be blocked before network call");

        assert_eq!(err.code, "admin_path_blocked");
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

    #[test]
    fn capability_negotiation_prefers_agent_contract_when_manifest_is_valid() {
        let response = ApiCallResult {
            status: 200,
            body: json!({
                "preferred_read_endpoint": "/v1/agent/context",
                "preferred_write_endpoint": "/v1/agent/write-with-proof",
                "min_mcp_version": "0.1.0"
            }),
            headers: None,
        };
        let profile = capability_profile_from_negotiation(Ok(response));

        assert_eq!(profile.mode, CapabilityMode::PreferredContract);
        assert_eq!(profile.effective_read_endpoint(), "/v1/agent/context");
        assert!(profile.supports_write_with_proof());
        assert_eq!(profile.reason, "capabilities_manifest_ok");
    }

    #[test]
    fn capability_negotiation_falls_back_for_legacy_server() {
        let response = ApiCallResult {
            status: 404,
            body: json!({
                "error": "not_found"
            }),
            headers: None,
        };
        let profile = capability_profile_from_negotiation(Ok(response));

        assert_eq!(profile.mode, CapabilityMode::LegacyFallback);
        assert_eq!(profile.effective_read_endpoint(), "/v1/projections");
        assert!(!profile.supports_write_with_proof());
        assert!(profile.reason.starts_with("capability_negotiation_http_"));
    }

    #[test]
    fn capability_negotiation_falls_back_on_min_version_mismatch() {
        let response = ApiCallResult {
            status: 200,
            body: json!({
                "preferred_read_endpoint": "/v1/agent/context",
                "preferred_write_endpoint": "/v1/agent/write-with-proof",
                "min_mcp_version": "999.0.0"
            }),
            headers: None,
        };
        let profile = capability_profile_from_negotiation(Ok(response));

        assert_eq!(profile.mode, CapabilityMode::LegacyFallback);
        assert_eq!(profile.reason, "capability_negotiation_version_mismatch");
        assert!(
            profile
                .warnings
                .iter()
                .any(|w| w.contains("min_mcp_version"))
        );
    }

    #[test]
    fn write_contract_surface_exposes_persist_intent_when_present() {
        let body = json!({
            "verification": {"status": "verified"},
            "claim_guard": {"allow_saved_claim": true},
            "persist_intent": {"mode": "auto_save", "status_label": "saved"}
        });
        let surface = write_contract_surface(&body);
        assert_eq!(surface["verification"]["status"], "verified");
        assert_eq!(surface["claim_guard"]["allow_saved_claim"], true);
        assert_eq!(surface["persist_intent"]["mode"], "auto_save");
        assert_eq!(surface["persist_intent"]["status_label"], "saved");
    }

    #[test]
    fn write_contract_surface_defaults_to_null_when_fields_missing() {
        let surface = write_contract_surface(&json!({"unexpected": true}));
        assert_eq!(surface["verification"], Value::Null);
        assert_eq!(surface["claim_guard"], Value::Null);
        assert_eq!(surface["persist_intent"], Value::Null);
    }
}
