use clap::Subcommand;
use serde_json::json;

use crate::util::{api_request, exit_error, read_json_from_file};

#[derive(Subcommand)]
pub enum EventCommands {
    /// Create a new event
    Create {
        /// Event type (e.g. "set.logged", "meal.logged", "metric.logged")
        #[arg(long)]
        event_type: String,
        /// Event timestamp (RFC3339). Defaults to now.
        #[arg(long)]
        timestamp: Option<String>,
        /// Event data as JSON string
        #[arg(long, required_unless_present = "data_file")]
        data: Option<String>,
        /// Read event data from file (use '-' for stdin)
        #[arg(long, short = 'f', conflicts_with = "data")]
        data_file: Option<String>,
        /// Idempotency key (auto-generated if omitted)
        #[arg(long)]
        idempotency_key: Option<String>,
        /// Source identifier (defaults to "cli")
        #[arg(long, default_value = "cli")]
        source: String,
        /// Agent identifier
        #[arg(long)]
        agent: Option<String>,
    },
    /// List events with optional filters
    List {
        /// Filter by event type
        #[arg(long)]
        event_type: Option<String>,
        /// Only events after this timestamp (RFC3339)
        #[arg(long)]
        since: Option<String>,
        /// Only events before this timestamp (RFC3339)
        #[arg(long)]
        until: Option<String>,
        /// Maximum number of events to return
        #[arg(long)]
        limit: Option<u32>,
        /// Pagination cursor from previous response
        #[arg(long)]
        cursor: Option<String>,
    },
    /// Create multiple events atomically
    Batch {
        /// JSON file with events array (use '-' for stdin)
        #[arg(long)]
        file: String,
    },
}

pub async fn run(api_url: &str, token: Option<&str>, command: EventCommands) -> i32 {
    match command {
        EventCommands::Create {
            event_type,
            timestamp,
            data,
            data_file,
            idempotency_key,
            source,
            agent,
        } => {
            create(
                api_url,
                token,
                &event_type,
                timestamp.as_deref(),
                data.as_deref(),
                data_file.as_deref(),
                idempotency_key.as_deref(),
                &source,
                agent.as_deref(),
            )
            .await
        }
        EventCommands::List {
            event_type,
            since,
            until,
            limit,
            cursor,
        } => {
            list(
                api_url,
                token,
                event_type.as_deref(),
                since.as_deref(),
                until.as_deref(),
                limit,
                cursor.as_deref(),
            )
            .await
        }
        EventCommands::Batch { file } => batch(api_url, token, &file).await,
    }
}

async fn create(
    api_url: &str,
    token: Option<&str>,
    event_type: &str,
    timestamp: Option<&str>,
    data: Option<&str>,
    data_file: Option<&str>,
    idempotency_key: Option<&str>,
    source: &str,
    agent: Option<&str>,
) -> i32 {
    let data_value: serde_json::Value = if let Some(d) = data {
        match serde_json::from_str(d) {
            Ok(v) => v,
            Err(e) => exit_error(
                &format!("Invalid JSON in --data: {e}"),
                Some("Provide valid JSON, e.g. --data '{\"weight_kg\":100}'"),
            ),
        }
    } else if let Some(f) = data_file {
        match read_json_from_file(f) {
            Ok(v) => v,
            Err(e) => exit_error(&e, Some("Provide a valid JSON file or use '-' for stdin")),
        }
    } else {
        exit_error(
            "Either --data or --data-file is required",
            Some("Use --data '{...}' or --data-file path.json"),
        )
    };

    let ts = match timestamp {
        Some(t) => t.to_string(),
        None => chrono::Utc::now().to_rfc3339(),
    };

    let idem_key = idempotency_key
        .map(|k| k.to_string())
        .unwrap_or_else(|| uuid::Uuid::now_v7().to_string());

    let mut metadata = json!({
        "source": source,
        "idempotency_key": idem_key
    });
    if let Some(a) = agent {
        metadata["agent"] = json!(a);
    }

    let body = json!({
        "timestamp": ts,
        "event_type": event_type,
        "data": data_value,
        "metadata": metadata
    });

    api_request(
        api_url,
        reqwest::Method::POST,
        "/v1/events",
        token,
        Some(body),
        &[],
        &[],
        false,
        false,
    )
    .await
}

async fn list(
    api_url: &str,
    token: Option<&str>,
    event_type: Option<&str>,
    since: Option<&str>,
    until: Option<&str>,
    limit: Option<u32>,
    cursor: Option<&str>,
) -> i32 {
    let mut query = Vec::new();
    if let Some(et) = event_type {
        query.push(("event_type".to_string(), et.to_string()));
    }
    if let Some(s) = since {
        query.push(("since".to_string(), s.to_string()));
    }
    if let Some(u) = until {
        query.push(("until".to_string(), u.to_string()));
    }
    if let Some(l) = limit {
        query.push(("limit".to_string(), l.to_string()));
    }
    if let Some(c) = cursor {
        query.push(("cursor".to_string(), c.to_string()));
    }

    api_request(
        api_url,
        reqwest::Method::GET,
        "/v1/events",
        token,
        None,
        &query,
        &[],
        false,
        false,
    )
    .await
}

async fn batch(api_url: &str, token: Option<&str>, file: &str) -> i32 {
    let body = match read_json_from_file(file) {
        Ok(v) => v,
        Err(e) => exit_error(&e, Some("Provide a JSON file with {\"events\": [...]}")),
    };

    api_request(
        api_url,
        reqwest::Method::POST,
        "/v1/events/batch",
        token,
        Some(body),
        &[],
        &[],
        false,
        false,
    )
    .await
}
