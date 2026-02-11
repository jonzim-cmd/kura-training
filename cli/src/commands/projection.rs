use clap::Subcommand;

use crate::util::api_request;

#[derive(Subcommand)]
pub enum ProjectionCommands {
    /// Get a single projection by type and key
    Get {
        /// Projection type (e.g. "exercise_progression")
        #[arg(long)]
        projection_type: String,
        /// Projection key (e.g. "squat")
        #[arg(long)]
        key: String,
    },
    /// List all projections of a given type
    List {
        /// Projection type (e.g. "exercise_progression")
        #[arg(long)]
        projection_type: String,
    },
}

pub async fn run(api_url: &str, token: Option<&str>, command: ProjectionCommands) -> i32 {
    match command {
        ProjectionCommands::Get {
            projection_type,
            key,
        } => get(api_url, token, &projection_type, &key).await,
        ProjectionCommands::List { projection_type } => {
            list(api_url, token, &projection_type).await
        }
    }
}

async fn get(api_url: &str, token: Option<&str>, projection_type: &str, key: &str) -> i32 {
    api_request(
        api_url,
        reqwest::Method::GET,
        &format!("/v1/projections/{projection_type}/{key}"),
        token,
        None,
        &[],
        &[],
        false,
        false,
    )
    .await
}

async fn list(api_url: &str, token: Option<&str>, projection_type: &str) -> i32 {
    api_request(
        api_url,
        reqwest::Method::GET,
        &format!("/v1/projections/{projection_type}"),
        token,
        None,
        &[],
        &[],
        false,
        false,
    )
    .await
}
