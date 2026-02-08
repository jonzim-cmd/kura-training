use clap::{Parser, Subcommand};
use serde_json::json;

#[derive(Parser)]
#[command(name = "kura", version, about = "Kura Training CLI — Agent interface for training, nutrition, and health data")]
struct Cli {
    /// API base URL
    #[arg(long, env = "KURA_API_URL", default_value = "http://localhost:3000")]
    api_url: String,

    /// User ID (temporary, will be replaced by auth)
    #[arg(long, env = "KURA_USER_ID")]
    user_id: Option<String>,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Check API health
    Health,
    /// Event operations
    Event {
        #[command(subcommand)]
        command: EventCommands,
    },
}

#[derive(Subcommand)]
enum EventCommands {
    /// Create a new event
    Create {
        /// Event type (e.g. "set.logged", "meal.logged", "metric.logged")
        #[arg(long)]
        event_type: String,
        /// Event timestamp (RFC3339). Defaults to now.
        #[arg(long)]
        timestamp: Option<String>,
        /// Event data as JSON string
        #[arg(long)]
        data: String,
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
}

fn client() -> reqwest::Client {
    reqwest::Client::new()
}

fn exit_error(message: &str, docs_hint: Option<&str>) -> ! {
    let mut err = json!({
        "error": "cli_error",
        "message": message
    });
    if let Some(hint) = docs_hint {
        err["docs_hint"] = json!(hint);
    }
    eprintln!("{}", serde_json::to_string_pretty(&err).unwrap());
    std::process::exit(1);
}

#[tokio::main]
async fn main() {
    let _ = dotenvy::dotenv();
    let cli = Cli::parse();

    let result = match cli.command {
        Commands::Health => health(&cli.api_url).await,
        Commands::Event { command } => {
            let user_id = cli.user_id.unwrap_or_else(|| {
                exit_error(
                    "user_id is required for event operations",
                    Some("Set --user-id or KURA_USER_ID env var"),
                );
            });
            match command {
                EventCommands::Create {
                    event_type,
                    timestamp,
                    data,
                    idempotency_key,
                    source,
                    agent,
                } => {
                    event_create(
                        &cli.api_url,
                        &user_id,
                        &event_type,
                        timestamp.as_deref(),
                        &data,
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
                    event_list(
                        &cli.api_url,
                        &user_id,
                        event_type.as_deref(),
                        since.as_deref(),
                        until.as_deref(),
                        limit,
                        cursor.as_deref(),
                    )
                    .await
                }
            }
        }
    };

    if let Err(e) = result {
        exit_error(&e.to_string(), None);
    }
}

async fn health(api_url: &str) -> Result<(), Box<dyn std::error::Error>> {
    let resp = client().get(format!("{api_url}/health")).send().await?;
    let body: serde_json::Value = resp.json().await?;
    println!("{}", serde_json::to_string_pretty(&body)?);
    Ok(())
}

async fn event_create(
    api_url: &str,
    user_id: &str,
    event_type: &str,
    timestamp: Option<&str>,
    data: &str,
    idempotency_key: Option<&str>,
    source: &str,
    agent: Option<&str>,
) -> Result<(), Box<dyn std::error::Error>> {
    let data_value: serde_json::Value = serde_json::from_str(data).map_err(|e| {
        format!("Invalid JSON in --data: {e}")
    })?;

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

    let resp = client()
        .post(format!("{api_url}/v1/events"))
        .header("x-user-id", user_id)
        .json(&body)
        .send()
        .await?;

    let status = resp.status();
    let resp_body: serde_json::Value = resp.json().await?;

    if !status.is_success() {
        eprintln!("{}", serde_json::to_string_pretty(&resp_body)?);
        std::process::exit(1);
    }

    println!("{}", serde_json::to_string_pretty(&resp_body)?);
    Ok(())
}

async fn event_list(
    api_url: &str,
    user_id: &str,
    event_type: Option<&str>,
    since: Option<&str>,
    until: Option<&str>,
    limit: Option<u32>,
    cursor: Option<&str>,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut url = format!("{api_url}/v1/events");
    let mut params = Vec::new();

    if let Some(et) = event_type {
        params.push(format!("event_type={et}"));
    }
    if let Some(s) = since {
        params.push(format!("since={s}"));
    }
    if let Some(u) = until {
        params.push(format!("until={u}"));
    }
    if let Some(l) = limit {
        params.push(format!("limit={l}"));
    }
    if let Some(c) = cursor {
        params.push(format!("cursor={c}"));
    }

    if !params.is_empty() {
        url = format!("{}?{}", url, params.join("&"));
    }

    let resp = client()
        .get(&url)
        .header("x-user-id", user_id)
        .send()
        .await?;

    let status = resp.status();
    let resp_body: serde_json::Value = resp.json().await?;

    if !status.is_success() {
        eprintln!("{}", serde_json::to_string_pretty(&resp_body)?);
        std::process::exit(1);
    }

    println!("{}", serde_json::to_string_pretty(&resp_body)?);
    Ok(())
}
