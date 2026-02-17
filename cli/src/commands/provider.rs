use clap::Subcommand;
use serde_json::json;
use uuid::Uuid;

use crate::util::{api_request, read_json_from_file};

#[derive(Subcommand)]
pub enum ProviderCommands {
    /// List provider connections
    List,
    /// Upsert provider connection metadata
    Upsert {
        /// Full JSON request payload (use '-' for stdin)
        #[arg(long)]
        request_file: String,
    },
    /// Revoke a provider connection by id
    Revoke {
        /// Provider connection UUID
        #[arg(long)]
        connection_id: Uuid,
        /// Revocation reason (audit field)
        #[arg(long)]
        reason: String,
    },
}

pub async fn run(api_url: &str, token: Option<&str>, command: ProviderCommands) -> i32 {
    match command {
        ProviderCommands::List => list(api_url, token).await,
        ProviderCommands::Upsert { request_file } => upsert(api_url, token, &request_file).await,
        ProviderCommands::Revoke {
            connection_id,
            reason,
        } => revoke(api_url, token, connection_id, &reason).await,
    }
}

async fn list(api_url: &str, token: Option<&str>) -> i32 {
    api_request(
        api_url,
        reqwest::Method::GET,
        "/v1/providers/connections",
        token,
        None,
        &[],
        &[],
        false,
        false,
    )
    .await
}

async fn upsert(api_url: &str, token: Option<&str>, request_file: &str) -> i32 {
    let body = match read_json_from_file(request_file) {
        Ok(v) => v,
        Err(e) => crate::util::exit_error(
            &e,
            Some("Provide a valid JSON provider-connection payload."),
        ),
    };

    api_request(
        api_url,
        reqwest::Method::POST,
        "/v1/providers/connections",
        token,
        Some(body),
        &[],
        &[],
        false,
        false,
    )
    .await
}

async fn revoke(api_url: &str, token: Option<&str>, connection_id: Uuid, reason: &str) -> i32 {
    let path = format!("/v1/providers/connections/{connection_id}/revoke");
    let body = json!({ "reason": reason });

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
