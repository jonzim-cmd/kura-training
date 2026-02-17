use serde::Deserialize;

use crate::error::AppError;

const TURNSTILE_VERIFY_URL: &str = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

#[derive(Debug, Deserialize)]
struct TurnstileVerifyResponse {
    success: bool,
    #[serde(default)]
    action: Option<String>,
    #[serde(default)]
    hostname: Option<String>,
    #[serde(rename = "error-codes", default)]
    error_codes: Vec<String>,
}

fn load_turnstile_secret() -> Result<String, AppError> {
    std::env::var("TURNSTILE_SECRET_KEY")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| AppError::Internal("TURNSTILE_SECRET_KEY must be configured".to_string()))
}

fn expected_turnstile_hostname() -> Option<String> {
    std::env::var("TURNSTILE_EXPECTED_HOSTNAME")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn validate_turnstile_response(
    verification: TurnstileVerifyResponse,
    expected_action: &str,
    expected_hostname: Option<&str>,
) -> Result<(), AppError> {
    if !verification.success {
        tracing::warn!(
            action = expected_action,
            error_codes = ?verification.error_codes,
            "Turnstile verification returned unsuccessful response"
        );
        return Err(AppError::Forbidden {
            message: "Captcha verification failed.".to_string(),
            docs_hint: Some("Please retry the security check.".to_string()),
        });
    }

    if verification.action.as_deref() != Some(expected_action) {
        tracing::warn!(
            expected_action = expected_action,
            actual_action = verification.action.as_deref().unwrap_or("<missing>"),
            "Turnstile action mismatch"
        );
        return Err(AppError::Forbidden {
            message: "Captcha validation context mismatch.".to_string(),
            docs_hint: Some("Please retry the security check.".to_string()),
        });
    }

    if let Some(expected_hostname) = expected_hostname {
        if verification.hostname.as_deref() != Some(expected_hostname) {
            tracing::warn!(
                expected_hostname = expected_hostname,
                actual_hostname = verification.hostname.as_deref().unwrap_or("<missing>"),
                "Turnstile hostname mismatch"
            );
            return Err(AppError::Forbidden {
                message: "Captcha validation host mismatch.".to_string(),
                docs_hint: Some("Please retry the security check.".to_string()),
            });
        }
    }

    Ok(())
}

pub async fn require_turnstile_token(
    token: Option<&str>,
    expected_action: &str,
) -> Result<(), AppError> {
    let token = token.map(str::trim).filter(|value| !value.is_empty());
    let token = token.ok_or_else(|| AppError::Validation {
        message: "Captcha verification is required.".to_string(),
        field: Some("turnstile_token".to_string()),
        received: None,
        docs_hint: Some("Complete the Turnstile challenge and resubmit.".to_string()),
    })?;

    let secret = load_turnstile_secret()?;
    let response = reqwest::Client::new()
        .post(TURNSTILE_VERIFY_URL)
        .form(&[("secret", secret.as_str()), ("response", token)])
        .send()
        .await
        .map_err(|_| AppError::Forbidden {
            message: "Captcha verification failed.".to_string(),
            docs_hint: Some("Please retry the security check.".to_string()),
        })?;

    if !response.status().is_success() {
        tracing::warn!(
            action = expected_action,
            status = %response.status(),
            "Turnstile verification request returned non-success status"
        );
        return Err(AppError::Forbidden {
            message: "Captcha verification failed.".to_string(),
            docs_hint: Some("Please retry the security check.".to_string()),
        });
    }

    let verification = response
        .json::<TurnstileVerifyResponse>()
        .await
        .map_err(|_| AppError::Forbidden {
            message: "Captcha verification failed.".to_string(),
            docs_hint: Some("Please retry the security check.".to_string()),
        })?;

    validate_turnstile_response(
        verification,
        expected_action,
        expected_turnstile_hostname().as_deref(),
    )
}

#[cfg(test)]
mod tests {
    use super::{TurnstileVerifyResponse, validate_turnstile_response};
    use crate::error::AppError;

    fn sample_verification(
        success: bool,
        action: Option<&str>,
        hostname: Option<&str>,
        error_codes: &[&str],
    ) -> TurnstileVerifyResponse {
        TurnstileVerifyResponse {
            success,
            action: action.map(str::to_string),
            hostname: hostname.map(str::to_string),
            error_codes: error_codes.iter().map(|value| value.to_string()).collect(),
        }
    }

    #[test]
    fn validate_turnstile_response_rejects_unsuccessful_verification() {
        let verification =
            sample_verification(false, Some("signup"), Some("withkura.com"), &["timeout"]);
        let err = validate_turnstile_response(verification, "signup", Some("withkura.com"))
            .expect_err("unsuccessful verification must be rejected");
        assert!(matches!(err, AppError::Forbidden { .. }));
    }

    #[test]
    fn validate_turnstile_response_rejects_action_mismatch() {
        let verification =
            sample_verification(true, Some("access_request"), Some("withkura.com"), &[]);
        let err = validate_turnstile_response(verification, "signup", Some("withkura.com"))
            .expect_err("unexpected action should be rejected");
        assert!(matches!(err, AppError::Forbidden { .. }));
    }

    #[test]
    fn validate_turnstile_response_rejects_hostname_mismatch() {
        let verification = sample_verification(true, Some("signup"), Some("evil.example"), &[]);
        let err = validate_turnstile_response(verification, "signup", Some("withkura.com"))
            .expect_err("unexpected hostname should be rejected");
        assert!(matches!(err, AppError::Forbidden { .. }));
    }

    #[test]
    fn validate_turnstile_response_accepts_expected_values() {
        let verification = sample_verification(true, Some("signup"), Some("withkura.com"), &[]);
        validate_turnstile_response(verification, "signup", Some("withkura.com"))
            .expect("verification should pass");
    }
}
