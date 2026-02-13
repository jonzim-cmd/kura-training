use std::io::Write;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::json;

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

    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&path)?;
    file.write_all(data.as_bytes())?;

    Ok(())
}

pub async fn resolve_token(api_url: &str) -> Result<String, Box<dyn std::error::Error>> {
    if let Ok(key) = std::env::var("KURA_API_KEY") {
        return Ok(key);
    }

    if let Some(creds) = load_credentials() {
        let buffer = chrono::Duration::minutes(5);
        if Utc::now() + buffer >= creds.expires_at {
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

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

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
