use clap::{Args, Subcommand};
use serde_json::json;
use uuid::Uuid;

use crate::util::{api_request, exit_error, read_json_from_file};

#[derive(Subcommand)]
pub enum ObservationCommands {
    /// Draft observation workflow
    Draft {
        #[command(subcommand)]
        command: ObservationDraftCommands,
    },
}

#[derive(Subcommand)]
pub enum ObservationDraftCommands {
    /// List open persist-intent drafts
    List {
        /// Max items to return (default: 20)
        #[arg(long)]
        limit: Option<i64>,
    },
    /// Show one draft observation in detail
    Show {
        /// Draft observation event id
        #[arg(long)]
        id: Uuid,
    },
    /// Promote draft into a formal event and retract the draft
    Promote(ObservationDraftPromoteArgs),
}

#[derive(Args)]
pub struct ObservationDraftPromoteArgs {
    /// Draft observation event id
    #[arg(long)]
    pub id: Uuid,
    /// Formal target event type (e.g. set.logged)
    #[arg(long)]
    pub event_type: String,
    /// Formal event payload as JSON string
    #[arg(long, required_unless_present = "data_file")]
    pub data: Option<String>,
    /// Read formal event payload from file (use '-' for stdin)
    #[arg(long, short = 'f', conflicts_with = "data")]
    pub data_file: Option<String>,
    /// Optional RFC3339 timestamp for formal event (default: now server-side)
    #[arg(long)]
    pub timestamp: Option<String>,
    /// Optional metadata.source override
    #[arg(long)]
    pub source: Option<String>,
    /// Optional metadata.agent override
    #[arg(long)]
    pub agent: Option<String>,
    /// Optional metadata.device override
    #[arg(long)]
    pub device: Option<String>,
    /// Optional metadata.session_id override
    #[arg(long)]
    pub session_id: Option<String>,
    /// Optional metadata.idempotency_key override
    #[arg(long)]
    pub idempotency_key: Option<String>,
    /// Optional retraction reason
    #[arg(long)]
    pub retract_reason: Option<String>,
}

pub async fn run(api_url: &str, token: Option<&str>, command: ObservationCommands) -> i32 {
    match command {
        ObservationCommands::Draft { command } => draft(api_url, token, command).await,
    }
}

async fn draft(api_url: &str, token: Option<&str>, command: ObservationDraftCommands) -> i32 {
    match command {
        ObservationDraftCommands::List { limit } => list_drafts(api_url, token, limit).await,
        ObservationDraftCommands::Show { id } => show_draft(api_url, token, id).await,
        ObservationDraftCommands::Promote(args) => promote_draft(api_url, token, args).await,
    }
}

async fn list_drafts(api_url: &str, token: Option<&str>, limit: Option<i64>) -> i32 {
    let mut query = Vec::new();
    if let Some(limit) = limit {
        query.push(("limit".to_string(), limit.to_string()));
    }
    api_request(
        api_url,
        reqwest::Method::GET,
        "/v1/agent/observation-drafts",
        token,
        None,
        &query,
        &[],
        false,
        false,
    )
    .await
}

async fn show_draft(api_url: &str, token: Option<&str>, id: Uuid) -> i32 {
    let path = format!("/v1/agent/observation-drafts/{id}");
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

fn parse_data_payload(data: Option<&str>, data_file: Option<&str>) -> serde_json::Value {
    if let Some(raw) = data {
        return serde_json::from_str(raw).unwrap_or_else(|e| {
            exit_error(
                &format!("Invalid JSON in --data: {e}"),
                Some("Provide valid JSON for --data"),
            )
        });
    }
    if let Some(path) = data_file {
        return read_json_from_file(path).unwrap_or_else(|e| {
            exit_error(
                &e,
                Some("Provide a valid JSON file for --data-file (or '-' for stdin)"),
            )
        });
    }
    exit_error(
        "Either --data or --data-file is required",
        Some("Provide formal event payload for promotion."),
    );
}

async fn promote_draft(
    api_url: &str,
    token: Option<&str>,
    args: ObservationDraftPromoteArgs,
) -> i32 {
    let data_payload = parse_data_payload(args.data.as_deref(), args.data_file.as_deref());
    let path = format!("/v1/agent/observation-drafts/{}/promote", args.id);
    let mut body = json!({
        "event_type": args.event_type,
        "data": data_payload,
    });
    if let Some(timestamp) = args.timestamp {
        body["timestamp"] = json!(timestamp);
    }
    if let Some(source) = args.source {
        body["source"] = json!(source);
    }
    if let Some(agent) = args.agent {
        body["agent"] = json!(agent);
    }
    if let Some(device) = args.device {
        body["device"] = json!(device);
    }
    if let Some(session_id) = args.session_id {
        body["session_id"] = json!(session_id);
    }
    if let Some(idempotency_key) = args.idempotency_key {
        body["idempotency_key"] = json!(idempotency_key);
    }
    if let Some(retract_reason) = args.retract_reason {
        body["retract_reason"] = json!(retract_reason);
    }
    api_request(
        api_url,
        reqwest::Method::POST,
        &path,
        token,
        Some(body),
        &[],
        &[],
        false,
        false,
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::parse_data_payload;
    use serde_json::json;

    #[test]
    fn parse_data_payload_accepts_inline_json() {
        let payload = parse_data_payload(Some(r#"{"reps": 5, "weight_kg": 100}"#), None);
        assert_eq!(payload, json!({"reps": 5, "weight_kg": 100}));
    }
}
