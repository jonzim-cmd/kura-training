use serde::Deserialize;
use serde_json::json;

use crate::util::{StoredCredentials, TokenResponse, client, config_path, save_credentials};

#[derive(Debug, Deserialize)]
struct DeviceAuthorizeResponse {
    device_code: String,
    user_code: String,
    verification_uri: String,
    verification_uri_complete: String,
    expires_in: i64,
    interval: i32,
}

pub async fn login(api_url: &str, device: bool) -> Result<(), Box<dyn std::error::Error>> {
    if device {
        return login_device(api_url).await;
    }
    login_browser(api_url).await
}

async fn login_browser(api_url: &str) -> Result<(), Box<dyn std::error::Error>> {
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
        return Err(format!(
            "Token exchange failed: {}",
            serde_json::to_string_pretty(&body)?
        )
        .into());
    }

    let token_resp: TokenResponse = resp.json().await?;

    let creds = StoredCredentials {
        api_url: api_url.to_string(),
        access_token: token_resp.access_token,
        refresh_token: token_resp.refresh_token,
        expires_at: chrono::Utc::now() + chrono::Duration::seconds(token_resp.expires_in),
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

async fn login_device(api_url: &str) -> Result<(), Box<dyn std::error::Error>> {
    let resp = client()
        .post(format!("{api_url}/v1/auth/device/authorize"))
        .json(&json!({
            "client_id": "kura-cli",
            "scope": ["agent:read", "agent:write", "agent:resolve"]
        }))
        .send()
        .await?;

    if !resp.status().is_success() {
        let body: serde_json::Value = resp.json().await?;
        return Err(format!(
            "Device authorization start failed: {}",
            serde_json::to_string_pretty(&body)?
        )
        .into());
    }

    let device: DeviceAuthorizeResponse = resp.json().await?;
    eprintln!("Open this URL to authenticate your device:");
    eprintln!("{}", device.verification_uri_complete);
    eprintln!(
        "Or open {} and enter code {}",
        device.verification_uri, device.user_code
    );
    let _ = open::that(&device.verification_uri_complete);

    let timeout = chrono::Duration::seconds(device.expires_in.max(30));
    let deadline = chrono::Utc::now() + timeout;
    let mut poll_interval = std::time::Duration::from_secs(device.interval.max(2) as u64);

    loop {
        if chrono::Utc::now() >= deadline {
            return Err(
                "Device authorization timed out. Start `kura login --device` again.".into(),
            );
        }

        let poll_resp = client()
            .post(format!("{api_url}/v1/auth/device/token"))
            .json(&json!({
                "device_code": device.device_code,
                "client_id": "kura-cli"
            }))
            .send()
            .await?;

        if poll_resp.status().is_success() {
            let token_resp: TokenResponse = poll_resp.json().await?;
            let creds = StoredCredentials {
                api_url: api_url.to_string(),
                access_token: token_resp.access_token,
                refresh_token: token_resp.refresh_token,
                expires_at: chrono::Utc::now() + chrono::Duration::seconds(token_resp.expires_in),
            };
            save_credentials(&creds)?;

            let output = json!({
                "status": "authenticated",
                "method": "device_code",
                "expires_at": creds.expires_at,
                "config_path": config_path().to_string_lossy()
            });
            println!("{}", serde_json::to_string_pretty(&output)?);
            return Ok(());
        }

        let body: serde_json::Value = poll_resp
            .json()
            .await
            .unwrap_or_else(|_| json!({"message":"unknown_error"}));
        let message = body
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or_default();

        match message {
            "authorization_pending" => {
                tokio::time::sleep(poll_interval).await;
            }
            "slow_down" => {
                poll_interval += std::time::Duration::from_secs(2);
                tokio::time::sleep(poll_interval).await;
            }
            "expired_token" => {
                return Err("Device code expired. Start `kura login --device` again.".into());
            }
            "access_denied" => {
                return Err("Device authorization denied.".into());
            }
            "invalid_device_code" | "invalid_grant" => {
                return Err("Device authorization invalid or already consumed.".into());
            }
            _ => {
                return Err(format!(
                    "Device token polling failed: {}",
                    serde_json::to_string_pretty(&body)?
                )
                .into());
            }
        }
    }
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

pub fn logout() -> Result<(), Box<dyn std::error::Error>> {
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
