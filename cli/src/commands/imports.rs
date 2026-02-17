use clap::Subcommand;
use uuid::Uuid;

use crate::util::{api_request, read_json_from_file};

#[derive(Subcommand)]
pub enum ImportCommands {
    /// Queue a new import job via /v1/imports/jobs
    Create {
        /// Full JSON request payload (use '-' for stdin)
        #[arg(long)]
        request_file: String,
    },
    /// Fetch import job status by id
    Status {
        /// Import job UUID
        #[arg(long)]
        job_id: Uuid,
    },
}

pub async fn run(api_url: &str, token: Option<&str>, command: ImportCommands) -> i32 {
    match command {
        ImportCommands::Create { request_file } => create(api_url, token, &request_file).await,
        ImportCommands::Status { job_id } => status(api_url, token, job_id).await,
    }
}

async fn create(api_url: &str, token: Option<&str>, request_file: &str) -> i32 {
    let body = match read_json_from_file(request_file) {
        Ok(v) => v,
        Err(e) => crate::util::exit_error(&e, Some("Provide a valid JSON import request payload.")),
    };

    api_request(
        api_url,
        reqwest::Method::POST,
        "/v1/imports/jobs",
        token,
        Some(body),
        &[],
        &[],
        false,
        false,
    )
    .await
}

async fn status(api_url: &str, token: Option<&str>, job_id: Uuid) -> i32 {
    let path = format!("/v1/imports/jobs/{job_id}");
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
