mod commands;
mod util;

use clap::{Parser, Subcommand};

use util::{exit_error, resolve_token};

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

    /// Get all projections in one call (agent bootstrap snapshot)
    Snapshot,

    /// Get system configuration (dimensions, conventions, event types)
    Config,

    /// Get agent context bundle (system + user profile + key dimensions)
    Context {
        /// Max exercise_progression projections to include (default: 5)
        #[arg(long)]
        exercise_limit: Option<u32>,
        /// Max custom projections to include (default: 10)
        #[arg(long)]
        custom_limit: Option<u32>,
    },

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

    /// Authenticate with the Kura API via OAuth (opens browser)
    Login,

    /// Remove stored credentials
    Logout,
}

#[tokio::main]
async fn main() {
    let _ = dotenvy::dotenv();
    let cli = Cli::parse();

    let code = match cli.command {
        Commands::Health => commands::health::run(&cli.api_url).await,

        Commands::Api(args) => commands::api::run(&cli.api_url, args).await,

        Commands::Event { command } => {
            let token = resolve_or_exit(&cli.api_url).await;
            commands::event::run(&cli.api_url, &token, command).await
        }

        Commands::Projection { command } => {
            let token = resolve_or_exit(&cli.api_url).await;
            commands::projection::run(&cli.api_url, &token, command).await
        }

        Commands::Snapshot => {
            let token = resolve_or_exit(&cli.api_url).await;
            commands::system::snapshot(&cli.api_url, &token).await
        }

        Commands::Config => {
            let token = resolve_or_exit(&cli.api_url).await;
            commands::system::config(&cli.api_url, &token).await
        }

        Commands::Context {
            exercise_limit,
            custom_limit,
        } => {
            let token = resolve_or_exit(&cli.api_url).await;
            commands::system::context(&cli.api_url, &token, exercise_limit, custom_limit).await
        }

        Commands::Doctor => commands::system::doctor(&cli.api_url).await,

        Commands::Discover { endpoints } => {
            commands::system::discover(&cli.api_url, endpoints).await
        }

        Commands::Account { command } => {
            let token = resolve_or_exit(&cli.api_url).await;
            commands::account::run(&cli.api_url, &token, command).await
        }

        Commands::Admin { command } => commands::admin::run(&cli.api_url, command).await,

        Commands::Login => {
            if let Err(e) = commands::auth::login(&cli.api_url).await {
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

async fn resolve_or_exit(api_url: &str) -> String {
    match resolve_token(api_url).await {
        Ok(t) => t,
        Err(e) => exit_error(
            &e.to_string(),
            Some("Run `kura login` or set KURA_API_KEY"),
        ),
    }
}
