use clap::{Parser, Subcommand};

use kura_cli::commands;
use kura_cli::util::{exit_error, resolve_token};

#[derive(Parser)]
#[command(
    name = "kura",
    version,
    about = "Kura Training CLI — Agent interface for training, nutrition, and health data"
)]
struct Cli {
    /// API base URL
    #[arg(long, env = "KURA_API_URL", default_value = "http://localhost:3000")]
    api_url: String,

    /// Skip credential check (for use behind an auth-injecting proxy)
    #[arg(long, env = "KURA_NO_AUTH")]
    no_auth: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Check API health
    Health,

    /// Access request operations
    Access {
        #[command(subcommand)]
        command: commands::access::AccessCommands,
    },

    /// Direct API access (like gh api — works with any endpoint)
    Api(commands::api::ApiArgs),

    /// Event operations (create, list, batch)
    Event {
        #[command(subcommand)]
        command: commands::event::EventCommands,
    },

    /// Projection operations (get, list)
    Projection {
        #[command(subcommand)]
        command: commands::projection::ProjectionCommands,
    },

    /// Agent operations (capabilities, context, write-with-proof, evidence, preferences, visualization)
    Agent {
        #[command(subcommand)]
        command: commands::agent::AgentCommands,
    },

    /// Observation workflows (draft visibility + promotion)
    Observation {
        #[command(subcommand)]
        command: commands::observation::ObservationCommands,
    },

    /// MCP server operations (Model Context Protocol over stdio)
    Mcp {
        #[command(subcommand)]
        command: commands::mcp::McpCommands,
    },

    /// External import job operations
    Import {
        #[command(subcommand)]
        command: commands::imports::ImportCommands,
    },

    /// Provider connection operations
    Provider {
        #[command(subcommand)]
        command: commands::provider::ProviderCommands,
    },

    /// Offline replay evaluation wrappers (worker-backed)
    Eval {
        #[command(subcommand)]
        command: commands::eval::EvalCommands,
    },

    /// Get all projections in one call (agent bootstrap snapshot)
    Snapshot,

    /// Get system configuration (dimensions, conventions, event types)
    Config,

    /// Legacy alias for `kura agent context`
    #[command(hide = true)]
    Context {
        /// Max exercise_progression projections to include (default: 5)
        #[arg(long)]
        exercise_limit: Option<u32>,
        /// Max strength_inference projections to include (default: 5)
        #[arg(long)]
        strength_limit: Option<u32>,
        /// Max custom projections to include (default: 10)
        #[arg(long)]
        custom_limit: Option<u32>,
        /// Optional task intent used for context ranking
        #[arg(long)]
        task_intent: Option<String>,
    },

    /// Legacy alias for `kura agent write-with-proof`
    #[command(hide = true)]
    WriteWithProof(commands::agent::WriteWithProofArgs),

    /// Diagnose setup: API, auth, worker, system config
    Doctor,

    /// Discover API endpoints (returns OpenAPI spec)
    Discover {
        /// Show compact endpoint list only (method, path, summary)
        #[arg(long)]
        endpoints: bool,
    },

    /// Account operations
    Account {
        #[command(subcommand)]
        command: commands::account::AccountCommands,
    },

    /// Admin operations (bootstrapping and user management)
    Admin {
        #[command(subcommand)]
        command: commands::admin::AdminCommands,
    },

    /// Authenticate with the Kura API via OAuth
    Login {
        /// Use OAuth Device Authorization flow (code entry in browser UI)
        #[arg(long)]
        device: bool,
    },

    /// Remove stored credentials
    Logout,
}

#[tokio::main]
async fn main() {
    let _ = dotenvy::dotenv();
    let cli = Cli::parse();

    let code = match cli.command {
        Commands::Health => commands::health::run(&cli.api_url).await,

        Commands::Access { command } => commands::access::run(&cli.api_url, command).await,

        Commands::Api(mut args) => {
            if cli.no_auth {
                args.no_auth = true;
            }
            commands::api::run(&cli.api_url, args).await
        }

        Commands::Event { command } => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::event::run(&cli.api_url, token.as_deref(), command).await
        }

        Commands::Projection { command } => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::projection::run(&cli.api_url, token.as_deref(), command).await
        }

        Commands::Agent { command } => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::agent::run(&cli.api_url, token.as_deref(), command).await
        }

        Commands::Observation { command } => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::observation::run(&cli.api_url, token.as_deref(), command).await
        }

        Commands::Mcp { command } => commands::mcp::run(&cli.api_url, cli.no_auth, command).await,

        Commands::Import { command } => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::imports::run(&cli.api_url, token.as_deref(), command).await
        }

        Commands::Provider { command } => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::provider::run(&cli.api_url, token.as_deref(), command).await
        }

        Commands::Eval { command } => commands::eval::run(command).await,

        Commands::Snapshot => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::system::snapshot(&cli.api_url, token.as_deref()).await
        }

        Commands::Config => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::system::config(&cli.api_url, token.as_deref()).await
        }

        Commands::Context {
            exercise_limit,
            strength_limit,
            custom_limit,
            task_intent,
        } => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::agent::context(
                &cli.api_url,
                token.as_deref(),
                exercise_limit,
                strength_limit,
                custom_limit,
                task_intent,
            )
            .await
        }

        Commands::WriteWithProof(args) => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::agent::write_with_proof(&cli.api_url, token.as_deref(), args).await
        }

        Commands::Doctor => commands::system::doctor(&cli.api_url).await,

        Commands::Discover { endpoints } => {
            commands::system::discover(&cli.api_url, endpoints).await
        }

        Commands::Account { command } => {
            let token = resolve_or_exit(&cli.api_url, cli.no_auth).await;
            commands::account::run(&cli.api_url, token.as_deref(), command).await
        }

        Commands::Admin { command } => {
            commands::admin::ensure_admin_surface_enabled_or_exit();
            let token = if commands::admin::requires_api_auth(&command) {
                resolve_or_exit(&cli.api_url, cli.no_auth).await
            } else {
                None
            };
            commands::admin::run(&cli.api_url, token.as_deref(), command).await
        }

        Commands::Login { device } => {
            if let Err(e) = commands::auth::login(&cli.api_url, device).await {
                exit_error(&e.to_string(), None);
            }
            0
        }

        Commands::Logout => {
            if let Err(e) = commands::auth::logout() {
                exit_error(&e.to_string(), None);
            }
            0
        }
    };

    std::process::exit(code);
}

async fn resolve_or_exit(api_url: &str, no_auth: bool) -> Option<String> {
    if no_auth {
        return None;
    }
    match resolve_token(api_url).await {
        Ok(t) => Some(t),
        Err(e) => exit_error(&e.to_string(), Some("Run `kura login` or set KURA_API_KEY")),
    }
}
