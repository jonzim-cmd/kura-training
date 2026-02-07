use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "kura", version, about = "Kura Training CLI — Agent interface for training, nutrition, and health data")]
struct Cli {
    /// API base URL
    #[arg(long, env = "KURA_API_URL", default_value = "http://localhost:3000")]
    api_url: String,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Check API health
    Health,
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();

    let result = match cli.command {
        Commands::Health => health(&cli.api_url).await,
    };

    if let Err(e) = result {
        let error = serde_json::json!({
            "error": "cli_error",
            "message": e.to_string()
        });
        eprintln!("{}", serde_json::to_string_pretty(&error).unwrap());
        std::process::exit(1);
    }
}

async fn health(api_url: &str) -> Result<(), Box<dyn std::error::Error>> {
    let resp = reqwest::get(format!("{api_url}/health")).await?;
    let body: serde_json::Value = resp.json().await?;
    println!("{}", serde_json::to_string_pretty(&body)?);
    Ok(())
}
