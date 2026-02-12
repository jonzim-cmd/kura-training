use std::io::Write;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::json;

/// Stored credentials for the CLI
#[derive(Debug, Serialize, Deserialize)]
pub struct StoredCredentials {
    pub api_url: String,
    pub access_token: String,
    pub refresh_token: String,
    pub expires_at: DateTime<Utc>,
}

#[derive(Deserialize)]
pub struct TokenResponse {
    pub access_token: String,
    pub refresh_token: String,
    pub expires_in: i64,
}

pub fn client() -> reqwest::Client {
    reqwest::Client::new()
}

pub fn exit_error(message: &str, docs_hint: Option<&str>) -> ! {
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

pub fn config_path() -> std::path::PathBuf {
    let config_dir = dirs::config_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("kura");
    config_dir.join("config.json")
}

pub fn load_credentials() -> Option<StoredCredentials> {
    let path = config_path();
    let data = std::fs::read_to_string(&path).ok()?;
    serde_json::from_str(&data).ok()
}

pub fn save_credentials(creds: &StoredCredentials) -> Result<(), Box<dyn std::error::Error>> {
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
pub async fn resolve_token(api_url: &str) -> Result<String, Box<dyn std::error::Error>> {
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
                    return Err(
                        "Access token expired and refresh failed. Run `kura login` again.".into(),
                    );
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

/// Execute an authenticated API request, print response, exit with structured code.
///
/// Exit codes: 0=success (2xx), 1=client error (4xx), 2=server error (5xx),
///             3=connection error, 4=usage error
pub async fn api_request(
    api_url: &str,
    method: reqwest::Method,
    path: &str,
    token: Option<&str>,
    body: Option<serde_json::Value>,
    query: &[(String, String)],
    extra_headers: &[(String, String)],
    raw: bool,
    include: bool,
) -> i32 {
    let url = match reqwest::Url::parse(&format!("{api_url}{path}")) {
        Ok(mut u) => {
            if !query.is_empty() {
                let mut q = u.query_pairs_mut();
                for (k, v) in query {
                    q.append_pair(k, v);
                }
            }
            u
        }
        Err(e) => {
            let err = json!({
                "error": "cli_error",
                "message": format!("Invalid URL: {api_url}{path}: {e}")
            });
            eprintln!("{}", serde_json::to_string_pretty(&err).unwrap());
            return 4;
        }
    };

    let mut req = client().request(method, url);

    if let Some(t) = token {
        req = req.header("Authorization", format!("Bearer {t}"));
    }

    for (k, v) in extra_headers {
        req = req.header(k.as_str(), v.as_str());
    }

    if let Some(b) = body {
        req = req.json(&b);
    }

    let resp = match req.send().await {
        Ok(r) => r,
        Err(e) => {
            let err = json!({
                "error": "connection_error",
                "message": format!("{e}"),
                "docs_hint": "Is the API server running? Check KURA_API_URL."
            });
            eprintln!("{}", serde_json::to_string_pretty(&err).unwrap());
            return 3;
        }
    };

    let status = resp.status().as_u16();
    let exit_code = match status {
        200..=299 => 0,
        400..=499 => 1,
        _ => 2,
    };

    // Collect headers before consuming response
    let headers: serde_json::Map<String, serde_json::Value> = if include {
        resp.headers()
            .iter()
            .map(|(k, v)| (k.to_string(), json!(v.to_str().unwrap_or("<binary>"))))
            .collect()
    } else {
        serde_json::Map::new()
    };

    let resp_body: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => json!({"raw_error": format!("Failed to parse response as JSON: {e}")}),
    };

    let output = if include {
        json!({
            "status": status,
            "headers": headers,
            "body": resp_body
        })
    } else {
        resp_body
    };

    let formatted = if raw {
        serde_json::to_string(&output).unwrap()
    } else {
        serde_json::to_string_pretty(&output).unwrap()
    };

    if exit_code == 0 {
        println!("{formatted}");
    } else {
        eprintln!("{formatted}");
    }

    exit_code
}

/// Execute a raw API request and return the response (no printing).
/// Used by doctor and other commands that need to inspect the response.
pub async fn raw_api_request(
    api_url: &str,
    method: reqwest::Method,
    path: &str,
    token: Option<&str>,
) -> Result<(u16, serde_json::Value), String> {
    let url = reqwest::Url::parse(&format!("{api_url}{path}"))
        .map_err(|e| format!("Invalid URL: {e}"))?;

    let mut req = client().request(method, url);
    if let Some(t) = token {
        req = req.header("Authorization", format!("Bearer {t}"));
    }

    let resp = req.send().await.map_err(|e| format!("{e}"))?;
    let status = resp.status().as_u16();
    let body: serde_json::Value = resp
        .json()
        .await
        .unwrap_or(json!({"error": "non-json response"}));

    Ok((status, body))
}

/// Check if auth is configured (without making a request).
/// Returns (method_name, detail) or None.
pub fn check_auth_configured() -> Option<(&'static str, String)> {
    if let Ok(key) = std::env::var("KURA_API_KEY") {
        let prefix = if key.len() > 12 { &key[..12] } else { &key };
        return Some(("api_key (env)", format!("{prefix}...")));
    }

    if let Some(creds) = load_credentials() {
        let expired = chrono::Utc::now() >= creds.expires_at;
        let detail = if expired {
            format!("expired at {}", creds.expires_at)
        } else {
            format!("valid until {}", creds.expires_at)
        };
        return Some(("oauth_token (stored)", detail));
    }

    None
}

/// Read JSON from a file path or stdin (when path is "-").
pub fn read_json_from_file(path: &str) -> Result<serde_json::Value, String> {
    let raw = if path == "-" {
        let mut buf = String::new();
        std::io::stdin()
            .read_line(&mut buf)
            .map_err(|e| format!("Failed to read stdin: {e}"))?;
        // Read remaining lines too
        let mut rest = String::new();
        while std::io::stdin()
            .read_line(&mut rest)
            .map_err(|e| format!("Failed to read stdin: {e}"))?
            > 0
        {
            buf.push_str(&rest);
            rest.clear();
        }
        buf
    } else {
        std::fs::read_to_string(path).map_err(|e| format!("Failed to read file '{path}': {e}"))?
    };
    serde_json::from_str(&raw).map_err(|e| format!("Invalid JSON in '{path}': {e}"))
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
