use clap::Subcommand;
use serde_json::json;
use uuid::Uuid;

use crate::util::{api_request, exit_error};

#[derive(Subcommand)]
pub enum AccountCommands {
    /// Permanently delete your account and all data (DSGVO Art. 17)
    Delete {
        /// Confirm deletion (required — no interactive prompts)
        #[arg(long)]
        confirm: bool,
    },
    /// Self-service API key operations
    ApiKey {
        #[command(subcommand)]
        command: ApiKeyCommands,
    },
}

#[derive(Subcommand)]
pub enum ApiKeyCommands {
    /// List API keys for the authenticated account
    List,
    /// Create a new API key for the authenticated account
    Create {
        /// Human-readable label
        #[arg(long)]
        label: String,
        /// Scope (repeatable). Defaults server-side when omitted.
        #[arg(long = "scope")]
        scopes: Vec<String>,
    },
    /// Revoke an API key by id
    Revoke {
        /// API key UUID
        #[arg(long)]
        key_id: Uuid,
    },
}

pub async fn run(api_url: &str, token: Option<&str>, command: AccountCommands) -> i32 {
    match command {
        AccountCommands::Delete { confirm } => delete(api_url, token, confirm).await,
        AccountCommands::ApiKey { command } => api_key(api_url, token, command).await,
    }
}

async fn delete(api_url: &str, token: Option<&str>, confirm: bool) -> i32 {
    if !confirm {
        exit_error(
            "Account deletion is permanent and irreversible. All events, projections, and credentials will be destroyed.",
            Some("Add --confirm to proceed: kura account delete --confirm"),
        );
    }

    api_request(
        api_url,
        reqwest::Method::DELETE,
        "/v1/account",
        token,
        None,
        &[],
        &[],
        false,
        false,
    )
    .await
}

async fn api_key(api_url: &str, token: Option<&str>, command: ApiKeyCommands) -> i32 {
    match command {
        ApiKeyCommands::List => {
            api_request(
                api_url,
                reqwest::Method::GET,
                "/v1/account/api-keys",
                token,
                None,
                &[],
                &[],
                false,
                false,
            )
            .await
        }
        ApiKeyCommands::Create { label, scopes } => {
            let mut body = json!({ "label": label });
            if !scopes.is_empty() {
                body["scopes"] = json!(scopes);
            }
            api_request(
                api_url,
                reqwest::Method::POST,
                "/v1/account/api-keys",
                token,
                Some(body),
                &[],
                &[],
                false,
                false,
            )
            .await
        }
        ApiKeyCommands::Revoke { key_id } => {
            let path = format!("/v1/account/api-keys/{key_id}");
            api_request(
                api_url,
                reqwest::Method::DELETE,
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
    }
}
