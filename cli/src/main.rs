use std::io::Write;

use chrono::{DateTime, Utc};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::json;

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
    /// Event operations
    Event {
        #[command(subcommand)]
        command: EventCommands,
    },
    /// Admin operations (direct database access for bootstrapping)
    Admin {
        #[command(subcommand)]
        command: AdminCommands,
    },
    /// Projection operations (read-optimized views computed from events)
    Projection {
        #[command(subcommand)]
        command: ProjectionCommands,
    },
    /// Authenticate with the Kura API via OAuth (opens browser)
    Login,
    /// Remove stored credentials
    Logout,
}

#[derive(Subcommand)]
enum ProjectionCommands {
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
        /// Event data as JSON string.
        // TODO: Add --data-file / stdin support for JSON input.
        // Shell special chars in exercise names (e.g. "Clean & Push Press")
        // break --data when passed directly. File/stdin input avoids this.
        // See: kura-training-hj7
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

#[derive(Subcommand)]
enum AdminCommands {
    /// Create a new user (requires DATABASE_URL)
    CreateUser {
        /// User email
        #[arg(long)]
        email: String,
        /// User password
        #[arg(long)]
        password: String,
        /// Display name
        #[arg(long)]
        display_name: Option<String>,
    },
    /// Create an API key for a user (requires DATABASE_URL)
    CreateKey {
        /// User UUID
        #[arg(long)]
        user_id: String,
        /// Human-readable label (e.g. "my-ci-server")
        #[arg(long)]
        label: String,
        /// Expiration in days (default: never)
        #[arg(long)]
        expires_in_days: Option<i64>,
    },
}

/// Stored credentials for the CLI
#[derive(Debug, Serialize, Deserialize)]
struct StoredCredentials {
    api_url: String,
    access_token: String,
    refresh_token: String,
    expires_at: DateTime<Utc>,
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

fn config_path() -> std::path::PathBuf {
    let config_dir = dirs::config_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("kura");
    config_dir.join("config.json")
}

fn load_credentials() -> Option<StoredCredentials> {
    let path = config_path();
    let data = std::fs::read_to_string(&path).ok()?;
    serde_json::from_str(&data).ok()
}

fn save_credentials(creds: &StoredCredentials) -> Result<(), Box<dyn std::error::Error>> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let data = serde_json::to_string_pretty(creds)?;

    // Write with restricted permissions (0o600)
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&path)?;
    file.write_all(data.as_bytes())?;

    Ok(())
}

/// Resolve a Bearer token for API requests (priority order):
/// 1. KURA_API_KEY env var
/// 2. ~/.config/kura/config.json (with auto-refresh)
/// 3. Error
async fn resolve_token(api_url: &str) -> Result<String, Box<dyn std::error::Error>> {
    // 1. Environment variable
    if let Ok(key) = std::env::var("KURA_API_KEY") {
        return Ok(key);
    }

    // 2. Stored credentials
    if let Some(creds) = load_credentials() {
        // Check if access token needs refresh (5-min buffer)
        let buffer = chrono::Duration::minutes(5);
        if Utc::now() + buffer >= creds.expires_at {
            // Try to refresh
            match refresh_stored_token(api_url, &creds).await {
                Ok(new_creds) => {
                    save_credentials(&new_creds)?;
                    return Ok(new_creds.access_token);
                }
                Err(_) => {
                    return Err("Access token expired and refresh failed. Run `kura login` again.".into());
                }
            }
        }
        return Ok(creds.access_token);
    }

    Err("No credentials found. Run `kura login` or set KURA_API_KEY.".into())
}

async fn refresh_stored_token(
    api_url: &str,
    creds: &StoredCredentials,
) -> Result<StoredCredentials, Box<dyn std::error::Error>> {
    let resp = client()
        .post(format!("{api_url}/v1/auth/token"))
        .json(&json!({
            "grant_type": "refresh_token",
            "refresh_token": creds.refresh_token,
            "client_id": "kura-cli"
        }))
        .send()
        .await?;

    if !resp.status().is_success() {
        let body: serde_json::Value = resp.json().await?;
        return Err(format!("Token refresh failed: {}", body).into());
    }

    let token_resp: TokenResponse = resp.json().await?;
    Ok(StoredCredentials {
        api_url: creds.api_url.clone(),
        access_token: token_resp.access_token,
        refresh_token: token_resp.refresh_token,
        expires_at: Utc::now() + chrono::Duration::seconds(token_resp.expires_in),
    })
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    refresh_token: String,
    expires_in: i64,
}

#[tokio::main]
async fn main() {
    let _ = dotenvy::dotenv();
    let cli = Cli::parse();

    let result = match cli.command {
        Commands::Health => health(&cli.api_url).await,
        Commands::Admin { command } => admin_command(command).await,
        Commands::Login => login(&cli.api_url).await,
        Commands::Logout => logout(),
        Commands::Projection { command } => {
            let token = match resolve_token(&cli.api_url).await {
                Ok(t) => t,
                Err(e) => exit_error(&e.to_string(), Some("Run `kura login` or set KURA_API_KEY")),
            };
            match command {
                ProjectionCommands::Get {
                    projection_type,
                    key,
                } => projection_get(&cli.api_url, &token, &projection_type, &key).await,
                ProjectionCommands::List { projection_type } => {
                    projection_list(&cli.api_url, &token, &projection_type).await
                }
            }
        }
        Commands::Event { command } => {
            let token = match resolve_token(&cli.api_url).await {
                Ok(t) => t,
                Err(e) => exit_error(&e.to_string(), Some("Run `kura login` or set KURA_API_KEY")),
            };
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
                        &token,
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
                        &token,
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

// ──────────────────────────────────────────────
// Admin commands (direct DB access)
// ──────────────────────────────────────────────

async fn admin_command(cmd: AdminCommands) -> Result<(), Box<dyn std::error::Error>> {
    let database_url = std::env::var("DATABASE_URL").map_err(|_| {
        "DATABASE_URL must be set for admin commands. \
         Admin commands connect directly to the database for bootstrapping."
    })?;

    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&database_url)
        .await?;

    match cmd {
        AdminCommands::CreateUser {
            email,
            password,
            display_name,
        } => admin_create_user(&pool, &email, &password, display_name.as_deref()).await,
        AdminCommands::CreateKey {
            user_id,
            label,
            expires_in_days,
        } => admin_create_key(&pool, &user_id, &label, expires_in_days).await,
    }
}

async fn admin_create_user(
    pool: &sqlx::PgPool,
    email: &str,
    password: &str,
    display_name: Option<&str>,
) -> Result<(), Box<dyn std::error::Error>> {
    let password_hash = kura_core::auth::hash_password(password)
        .map_err(|e| format!("Failed to hash password: {e}"))?;

    let user_id = uuid::Uuid::now_v7();

    sqlx::query(
        "INSERT INTO users (id, email, password_hash, display_name) VALUES ($1, $2, $3, $4)",
    )
    .bind(user_id)
    .bind(email)
    .bind(&password_hash)
    .bind(display_name)
    .execute(pool)
    .await?;

    let output = json!({
        "user_id": user_id,
        "email": email,
        "display_name": display_name
    });
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

async fn admin_create_key(
    pool: &sqlx::PgPool,
    user_id_str: &str,
    label: &str,
    expires_in_days: Option<i64>,
) -> Result<(), Box<dyn std::error::Error>> {
    let user_id = uuid::Uuid::parse_str(user_id_str)?;
    let (full_key, key_hash) = kura_core::auth::generate_api_key();
    let prefix = kura_core::auth::key_prefix(&full_key);
    let key_id = uuid::Uuid::now_v7();

    let expires_at = expires_in_days.map(|d| Utc::now() + chrono::Duration::days(d));

    sqlx::query(
        "INSERT INTO api_keys (id, user_id, key_hash, key_prefix, label, expires_at) \
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(key_id)
    .bind(user_id)
    .bind(&key_hash)
    .bind(&prefix)
    .bind(label)
    .bind(expires_at)
    .execute(pool)
    .await?;

    let output = json!({
        "key_id": key_id,
        "api_key": full_key,
        "key_prefix": prefix,
        "label": label,
        "expires_at": expires_at,
        "warning": "Store this key securely. It will NOT be shown again."
    });
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

// ──────────────────────────────────────────────
// Login / Logout (OAuth + PKCE)
// ──────────────────────────────────────────────

async fn login(api_url: &str) -> Result<(), Box<dyn std::error::Error>> {
    let code_verifier = kura_core::auth::generate_code_verifier();
    let code_challenge = kura_core::auth::generate_code_challenge(&code_verifier);
    let state = kura_core::auth::generate_code_verifier(); // reuse for random state

    // Start local callback server on random port
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let port = listener.local_addr()?.port();
    let redirect_uri = format!("http://127.0.0.1:{port}/callback");

    let authorize_url = format!(
        "{api_url}/v1/auth/authorize\
         ?response_type=code\
         &client_id=kura-cli\
         &redirect_uri={redirect_uri}\
         &code_challenge={code_challenge}\
         &code_challenge_method=S256\
         &state={state}"
    );

    eprintln!("Opening browser for authentication...");
    eprintln!("If the browser doesn't open, visit: {authorize_url}");

    let _ = open::that(&authorize_url);

    // Wait for callback (5 min timeout)
    let callback_result = tokio::select! {
        result = wait_for_callback(listener) => result,
        _ = tokio::time::sleep(std::time::Duration::from_secs(300)) => {
            return Err("Login timed out after 5 minutes.".into());
        }
    };

    let (received_code, received_state) = callback_result?;

    // Verify state
    if received_state.as_deref() != Some(state.as_str()) {
        return Err("OAuth state mismatch — possible CSRF attack.".into());
    }

    // Exchange code for tokens
    let resp = client()
        .post(format!("{api_url}/v1/auth/token"))
        .json(&json!({
            "grant_type": "authorization_code",
            "code": received_code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": "kura-cli"
        }))
        .send()
        .await?;

    if !resp.status().is_success() {
        let body: serde_json::Value = resp.json().await?;
        return Err(format!("Token exchange failed: {}", serde_json::to_string_pretty(&body)?).into());
    }

    let token_resp: TokenResponse = resp.json().await?;

    let creds = StoredCredentials {
        api_url: api_url.to_string(),
        access_token: token_resp.access_token,
        refresh_token: token_resp.refresh_token,
        expires_at: Utc::now() + chrono::Duration::seconds(token_resp.expires_in),
    };

    save_credentials(&creds)?;

    let output = json!({
        "status": "authenticated",
        "expires_at": creds.expires_at,
        "config_path": config_path().to_string_lossy()
    });
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

async fn wait_for_callback(
    listener: tokio::net::TcpListener,
) -> Result<(String, Option<String>), Box<dyn std::error::Error>> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let (mut stream, _) = listener.accept().await?;
    let mut buf = vec![0u8; 4096];
    let n = stream.read(&mut buf).await?;
    let request = String::from_utf8_lossy(&buf[..n]);

    // Parse GET /callback?code=...&state=... HTTP/1.1
    let path = request
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or("");

    let url = url::Url::parse(&format!("http://localhost{path}"))
        .map_err(|e| format!("Failed to parse callback URL: {e}"))?;

    let code = url
        .query_pairs()
        .find(|(k, _)| k == "code")
        .map(|(_, v): (_, _)| v.to_string())
        .ok_or("No 'code' parameter in callback")?;

    let state = url
        .query_pairs()
        .find(|(k, _)| k == "state")
        .map(|(_, v): (_, _)| v.to_string());

    // Send success response to browser
    let response = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n\
        <html><body><h1>Authenticated!</h1><p>You can close this tab.</p></body></html>";
    stream.write_all(response.as_bytes()).await?;
    stream.shutdown().await?;

    Ok((code, state))
}

fn logout() -> Result<(), Box<dyn std::error::Error>> {
    let path = config_path();
    if path.exists() {
        std::fs::remove_file(&path)?;
    }
    let output = json!({
        "status": "logged_out",
        "config_path": path.to_string_lossy()
    });
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

// ──────────────────────────────────────────────
// Health
// ──────────────────────────────────────────────

async fn health(api_url: &str) -> Result<(), Box<dyn std::error::Error>> {
    let resp = client().get(format!("{api_url}/health")).send().await?;
    let body: serde_json::Value = resp.json().await?;
    println!("{}", serde_json::to_string_pretty(&body)?);
    Ok(())
}

// ──────────────────────────────────────────────
// Event commands (authenticated via Bearer token)
// ──────────────────────────────────────────────

async fn event_create(
    api_url: &str,
    token: &str,
    event_type: &str,
    timestamp: Option<&str>,
    data: &str,
    idempotency_key: Option<&str>,
    source: &str,
    agent: Option<&str>,
) -> Result<(), Box<dyn std::error::Error>> {
    let data_value: serde_json::Value =
        serde_json::from_str(data).map_err(|e| format!("Invalid JSON in --data: {e}"))?;

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
        .header("Authorization", format!("Bearer {token}"))
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
    token: &str,
    event_type: Option<&str>,
    since: Option<&str>,
    until: Option<&str>,
    limit: Option<u32>,
    cursor: Option<&str>,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut url = reqwest::Url::parse(&format!("{api_url}/v1/events"))?;

    {
        let mut q = url.query_pairs_mut();
        if let Some(et) = event_type {
            q.append_pair("event_type", et);
        }
        if let Some(s) = since {
            q.append_pair("since", s);
        }
        if let Some(u) = until {
            q.append_pair("until", u);
        }
        if let Some(l) = limit {
            q.append_pair("limit", &l.to_string());
        }
        if let Some(c) = cursor {
            q.append_pair("cursor", c);
        }
    }

    let resp = client()
        .get(url)
        .header("Authorization", format!("Bearer {token}"))
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

// ──────────────────────────────────────────────
// Projection commands (authenticated via Bearer token)
// ──────────────────────────────────────────────

async fn projection_get(
    api_url: &str,
    token: &str,
    projection_type: &str,
    key: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let url = format!("{api_url}/v1/projections/{projection_type}/{key}");

    let resp = client()
        .get(&url)
        .header("Authorization", format!("Bearer {token}"))
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

async fn projection_list(
    api_url: &str,
    token: &str,
    projection_type: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let url = format!("{api_url}/v1/projections/{projection_type}");

    let resp = client()
        .get(&url)
        .header("Authorization", format!("Bearer {token}"))
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

// Unix-specific imports for file permissions
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

// No-op on non-unix (won't compile for Windows without this)
#[cfg(not(unix))]
trait OpenOptionsExt {
    fn mode(&mut self, _mode: u32) -> &mut Self;
}

#[cfg(not(unix))]
impl OpenOptionsExt for std::fs::OpenOptions {
    fn mode(&mut self, _mode: u32) -> &mut Self {
        self
    }
}
