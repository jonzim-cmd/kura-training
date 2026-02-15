use clap::{Args, Subcommand};
use serde_json::json;
use uuid::Uuid;

use crate::util::{api_request, exit_error, read_json_from_file};

#[derive(Subcommand)]
pub enum AgentCommands {
    /// Get agent context bundle (system + user profile + key dimensions)
    Context {
        /// Max exercise_progression projections to include (default: 5)
        #[arg(long)]
        exercise_limit: Option<u32>,
        /// Max strength_inference projections to include (default: 5)
        #[arg(long)]
        strength_limit: Option<u32>,
        /// Max custom projections to include (default: 10)
        #[arg(long)]
        custom_limit: Option<u32>,
        /// Optional task intent used for context ranking (e.g. "dunk progression")
        #[arg(long)]
        task_intent: Option<String>,
    },
    /// Write events with receipts + read-after-write verification
    WriteWithProof(WriteWithProofArgs),
    /// Evidence lineage operations
    Evidence {
        #[command(subcommand)]
        command: AgentEvidenceCommands,
    },
    /// Direct agent API access under /v1/agent/*
    Request(AgentRequestArgs),
}

#[derive(Subcommand)]
pub enum AgentEvidenceCommands {
    /// Explain lineage claims for one persisted event
    Event {
        /// Target event UUID
        #[arg(long)]
        event_id: Uuid,
    },
}

#[derive(Args)]
pub struct AgentRequestArgs {
    /// HTTP method (GET, POST, PUT, DELETE, PATCH)
    pub method: String,

    /// Agent path: relative (e.g. context) or absolute (/v1/agent/context)
    pub path: String,

    /// Request body as JSON string
    #[arg(long, short = 'd')]
    pub data: Option<String>,

    /// Read request body from file (use '-' for stdin)
    #[arg(long, short = 'f', conflicts_with = "data")]
    pub data_file: Option<String>,

    /// Query parameters (repeatable: key=value)
    #[arg(long, short = 'q')]
    pub query: Vec<String>,

    /// Extra headers (repeatable: Key:Value)
    #[arg(long, short = 'H')]
    pub header: Vec<String>,

    /// Skip pretty-printing (raw JSON for piping)
    #[arg(long)]
    pub raw: bool,

    /// Include HTTP status and headers in response wrapper
    #[arg(long, short = 'i')]
    pub include: bool,
}

#[derive(Args)]
pub struct WriteWithProofArgs {
    /// JSON file containing events array or {"events":[...]} (use '-' for stdin)
    #[arg(
        long,
        required_unless_present = "request_file",
        conflicts_with = "request_file"
    )]
    pub events_file: Option<String>,

    /// Read-after-write target in projection_type:key format (repeatable)
    #[arg(
        long,
        required_unless_present = "request_file",
        conflicts_with = "request_file"
    )]
    pub target: Vec<String>,

    /// Max verification wait in milliseconds (100..10000)
    #[arg(long)]
    pub verify_timeout_ms: Option<u64>,

    /// Full request payload JSON file for /v1/agent/write-with-proof
    #[arg(long, conflicts_with_all = ["events_file", "target", "verify_timeout_ms"])]
    pub request_file: Option<String>,
}

pub async fn run(api_url: &str, token: Option<&str>, command: AgentCommands) -> i32 {
    match command {
        AgentCommands::Context {
            exercise_limit,
            strength_limit,
            custom_limit,
            task_intent,
        } => context(
            api_url,
            token,
            exercise_limit,
            strength_limit,
            custom_limit,
            task_intent,
        )
        .await,
        AgentCommands::WriteWithProof(args) => write_with_proof(api_url, token, args).await,
        AgentCommands::Evidence { command } => match command {
            AgentEvidenceCommands::Event { event_id } => {
                evidence_event(api_url, token, event_id).await
            }
        },
        AgentCommands::Request(args) => request(api_url, token, args).await,
    }
}

pub async fn context(
    api_url: &str,
    token: Option<&str>,
    exercise_limit: Option<u32>,
    strength_limit: Option<u32>,
    custom_limit: Option<u32>,
    task_intent: Option<String>,
) -> i32 {
    let mut query = Vec::new();
    if let Some(v) = exercise_limit {
        query.push(("exercise_limit".to_string(), v.to_string()));
    }
    if let Some(v) = strength_limit {
        query.push(("strength_limit".to_string(), v.to_string()));
    }
    if let Some(v) = custom_limit {
        query.push(("custom_limit".to_string(), v.to_string()));
    }
    if let Some(v) = task_intent {
        query.push(("task_intent".to_string(), v));
    }

    api_request(
        api_url,
        reqwest::Method::GET,
        "/v1/agent/context",
        token,
        None,
        &query,
        &[],
        false,
        false,
    )
    .await
}

async fn evidence_event(api_url: &str, token: Option<&str>, event_id: Uuid) -> i32 {
    let path = format!("/v1/agent/evidence/event/{event_id}");
    api_request(
        api_url,
        reqwest::Method::GET,
        &path,
        token,
        None,
        &[],
        &[],
        false,
        false,
    )
    .await
}

async fn request(api_url: &str, token: Option<&str>, args: AgentRequestArgs) -> i32 {
    let method = parse_method(&args.method);
    let path = normalize_agent_path(&args.path);
    let query = parse_query_pairs(&args.query);
    let headers = parse_headers(&args.header);
    let body = resolve_body(args.data.as_deref(), args.data_file.as_deref());

    api_request(
        api_url,
        method,
        &path,
        token,
        body,
        &query,
        &headers,
        args.raw,
        args.include,
    )
    .await
}

pub async fn write_with_proof(api_url: &str, token: Option<&str>, args: WriteWithProofArgs) -> i32 {
    let body = if let Some(file) = args.request_file.as_deref() {
        load_full_request(file)
    } else {
        build_request_from_events_and_targets(
            args.events_file.as_deref().unwrap_or(""),
            &args.target,
            args.verify_timeout_ms,
        )
    };

    api_request(
        api_url,
        reqwest::Method::POST,
        "/v1/agent/write-with-proof",
        token,
        Some(body),
        &[],
        &[],
        false,
        false,
    )
    .await
}

fn parse_method(raw: &str) -> reqwest::Method {
    match raw.to_uppercase().as_str() {
        "GET" => reqwest::Method::GET,
        "POST" => reqwest::Method::POST,
        "PUT" => reqwest::Method::PUT,
        "DELETE" => reqwest::Method::DELETE,
        "PATCH" => reqwest::Method::PATCH,
        "HEAD" => reqwest::Method::HEAD,
        "OPTIONS" => reqwest::Method::OPTIONS,
        other => exit_error(
            &format!("Unknown HTTP method: {other}"),
            Some("Supported methods: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"),
        ),
    }
}

fn normalize_agent_path(raw: &str) -> String {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        exit_error(
            "Agent path must not be empty.",
            Some("Use relative path like 'context' or absolute path '/v1/agent/context'."),
        );
    }

    if trimmed.starts_with("/v1/agent") {
        return trimmed.to_string();
    }
    if trimmed.starts_with("v1/agent") {
        return format!("/{trimmed}");
    }
    if trimmed.starts_with('/') {
        exit_error(
            &format!("Invalid agent path '{trimmed}'."),
            Some(
                "`kura agent request` only supports /v1/agent/* paths. Use `kura api` for other endpoints.",
            ),
        );
    }

    format!("/v1/agent/{}", trimmed.trim_start_matches('/'))
}

fn parse_query_pairs(raw: &[String]) -> Vec<(String, String)> {
    raw.iter()
        .map(|entry| {
            entry.split_once('=').map_or_else(
                || {
                    exit_error(
                        &format!("Invalid query parameter: '{entry}'"),
                        Some("Format: key=value, e.g. --query event_type=set.logged"),
                    )
                },
                |(k, v)| (k.to_string(), v.to_string()),
            )
        })
        .collect()
}

fn parse_headers(raw: &[String]) -> Vec<(String, String)> {
    raw.iter()
        .map(|entry| {
            entry.split_once(':').map_or_else(
                || {
                    exit_error(
                        &format!("Invalid header: '{entry}'"),
                        Some("Format: Key:Value, e.g. --header Content-Type:application/json"),
                    )
                },
                |(k, v)| (k.trim().to_string(), v.trim().to_string()),
            )
        })
        .collect()
}

fn resolve_body(data: Option<&str>, data_file: Option<&str>) -> Option<serde_json::Value> {
    if let Some(raw) = data {
        match serde_json::from_str(raw) {
            Ok(v) => return Some(v),
            Err(e) => exit_error(
                &format!("Invalid JSON in --data: {e}"),
                Some("Provide valid JSON string"),
            ),
        }
    }

    if let Some(file) = data_file {
        return match read_json_from_file(file) {
            Ok(v) => Some(v),
            Err(e) => exit_error(&e, Some("Provide a valid JSON file or use '-' for stdin")),
        };
    }

    None
}

fn load_full_request(path: &str) -> serde_json::Value {
    let payload = match read_json_from_file(path) {
        Ok(v) => v,
        Err(e) => exit_error(
            &e,
            Some(
                "Provide JSON with events, read_after_write_targets, and optional verify_timeout_ms.",
            ),
        ),
    };
    if payload
        .get("events")
        .and_then(|value| value.as_array())
        .is_none()
    {
        exit_error(
            "request payload must include an events array",
            Some(
                "Use --request-file with {\"events\": [...], \"read_after_write_targets\": [...]}",
            ),
        );
    }
    if payload
        .get("read_after_write_targets")
        .and_then(|value| value.as_array())
        .is_none()
    {
        exit_error(
            "request payload must include read_after_write_targets array",
            Some("Set read_after_write_targets to [{\"projection_type\":\"...\",\"key\":\"...\"}]"),
        );
    }
    payload
}

fn build_request_from_events_and_targets(
    events_file: &str,
    raw_targets: &[String],
    verify_timeout_ms: Option<u64>,
) -> serde_json::Value {
    if raw_targets.is_empty() {
        exit_error(
            "--target is required when --request-file is not used",
            Some("Repeat --target projection_type:key for read-after-write checks."),
        );
    }

    let parsed_targets = parse_targets(raw_targets);
    let events_payload = match read_json_from_file(events_file) {
        Ok(v) => v,
        Err(e) => exit_error(
            &e,
            Some("Provide --events-file as JSON array or object with events array."),
        ),
    };

    let events = extract_events_array(events_payload);
    build_write_with_proof_request(events, parsed_targets, verify_timeout_ms)
}

fn parse_targets(raw_targets: &[String]) -> Vec<serde_json::Value> {
    raw_targets
        .iter()
        .map(|raw| {
            let (projection_type, key) = raw.split_once(':').unwrap_or_else(|| {
                exit_error(
                    &format!("Invalid --target '{raw}'"),
                    Some("Use format projection_type:key, e.g. user_profile:me"),
                )
            });
            let projection_type = projection_type.trim();
            let key = key.trim();
            if projection_type.is_empty() || key.is_empty() {
                exit_error(
                    &format!("Invalid --target '{raw}'"),
                    Some("projection_type and key must both be non-empty."),
                );
            }
            json!({
                "projection_type": projection_type,
                "key": key,
            })
        })
        .collect()
}

fn extract_events_array(events_payload: serde_json::Value) -> Vec<serde_json::Value> {
    if let Some(events) = events_payload.as_array() {
        return events.to_vec();
    }
    if let Some(events) = events_payload
        .get("events")
        .and_then(|value| value.as_array())
    {
        return events.to_vec();
    }
    exit_error(
        "events payload must be an array or object with events array",
        Some("Example: --events-file events.json where file is [{...}] or {\"events\": [{...}]}"),
    );
}

fn build_write_with_proof_request(
    events: Vec<serde_json::Value>,
    parsed_targets: Vec<serde_json::Value>,
    verify_timeout_ms: Option<u64>,
) -> serde_json::Value {
    let mut request = json!({
        "events": events,
        "read_after_write_targets": parsed_targets,
    });
    if let Some(timeout) = verify_timeout_ms {
        request["verify_timeout_ms"] = json!(timeout);
    }
    request
}

#[cfg(test)]
mod tests {
    use super::{
        build_write_with_proof_request, extract_events_array, normalize_agent_path, parse_method,
        parse_targets,
    };
    use serde_json::json;

    #[test]
    fn normalize_agent_path_accepts_relative_path() {
        assert_eq!(
            normalize_agent_path("evidence/event/abc"),
            "/v1/agent/evidence/event/abc"
        );
    }

    #[test]
    fn normalize_agent_path_accepts_absolute_agent_path() {
        assert_eq!(
            normalize_agent_path("/v1/agent/context"),
            "/v1/agent/context"
        );
    }

    #[test]
    fn parse_method_accepts_standard_http_methods() {
        for method in &[
            "get", "GET", "post", "PUT", "delete", "patch", "head", "OPTIONS",
        ] {
            let parsed = parse_method(method);
            assert!(!parsed.as_str().is_empty());
        }
    }

    #[test]
    fn parse_targets_accepts_projection_type_key_format() {
        let parsed = parse_targets(&[
            "user_profile:me".to_string(),
            "training_timeline:overview".to_string(),
        ]);
        assert_eq!(parsed[0]["projection_type"], "user_profile");
        assert_eq!(parsed[0]["key"], "me");
        assert_eq!(parsed[1]["projection_type"], "training_timeline");
        assert_eq!(parsed[1]["key"], "overview");
    }

    #[test]
    fn extract_events_array_supports_plain_array() {
        let events = extract_events_array(json!([
            {"event_type":"set.logged"},
            {"event_type":"metric.logged"}
        ]));
        assert_eq!(events.len(), 2);
    }

    #[test]
    fn extract_events_array_supports_object_wrapper() {
        let events = extract_events_array(json!({
            "events": [{"event_type":"set.logged"}]
        }));
        assert_eq!(events.len(), 1);
    }

    #[test]
    fn build_write_with_proof_request_serializes_expected_fields() {
        let request = build_write_with_proof_request(
            vec![json!({"event_type":"set.logged"})],
            vec![json!({"projection_type":"user_profile","key":"me"})],
            Some(1200),
        );
        assert_eq!(request["events"].as_array().unwrap().len(), 1);
        assert_eq!(
            request["read_after_write_targets"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(request["verify_timeout_ms"], 1200);
    }
}
