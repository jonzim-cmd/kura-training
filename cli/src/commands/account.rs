use clap::Subcommand;

use crate::util::{api_request, exit_error};

#[derive(Subcommand)]
pub enum AccountCommands {
    /// Permanently delete your account and all data (DSGVO Art. 17)
    Delete {
        /// Confirm deletion (required — no interactive prompts)
        #[arg(long)]
        confirm: bool,
    },
}

pub async fn run(api_url: &str, token: Option<&str>, command: AccountCommands) -> i32 {
    match command {
        AccountCommands::Delete { confirm } => delete(api_url, token, confirm).await,
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
