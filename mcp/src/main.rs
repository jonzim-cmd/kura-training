use clap::Parser;

use kura_cli::commands::mcp::{McpCommands, run as run_mcp};

#[derive(Parser)]
#[command(
    name = "kura-mcp",
    version,
    about = "Kura MCP server — dedicated MCP runtime over stdio"
)]
struct Cli {
    /// API base URL
    #[arg(long, env = "KURA_API_URL", default_value = "http://localhost:3000")]
    api_url: String,

    /// Skip credential check (for use behind an auth-injecting proxy)
    #[arg(long, env = "KURA_NO_AUTH")]
    no_auth: bool,

    #[command(subcommand)]
    command: McpCommands,
}

#[tokio::main]
async fn main() {
    let _ = dotenvy::dotenv();
    let cli = Cli::parse();

    let code = run_mcp(&cli.api_url, cli.no_auth, cli.command).await;
    std::process::exit(code);
}
