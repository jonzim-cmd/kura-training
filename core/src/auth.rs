use argon2::Argon2;
use base64::Engine;
use password_hash::rand_core::OsRng;
use password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use rand::Rng;
use sha2::{Digest, Sha256};

/// Generate an API key. Returns `(full_key, sha256_hash)`.
/// Key format: `kura_sk_` + 32 random bytes hex-encoded.
pub fn generate_api_key() -> (String, String) {
    let raw = random_hex(32);
    let full_key = format!("kura_sk_{raw}");
    let hash = hash_token(&full_key);
    (full_key, hash)
}

/// Generate an access token. Returns `(full_token, sha256_hash)`.
/// Format: `kura_at_` + 32 random bytes hex-encoded.
pub fn generate_access_token() -> (String, String) {
    let raw = random_hex(32);
    let full_token = format!("kura_at_{raw}");
    let hash = hash_token(&full_token);
    (full_token, hash)
}

/// Generate a refresh token. Returns `(full_token, sha256_hash)`.
/// Format: `kura_rt_` + 32 random bytes hex-encoded.
pub fn generate_refresh_token() -> (String, String) {
    let raw = random_hex(32);
    let full_token = format!("kura_rt_{raw}");
    let hash = hash_token(&full_token);
    (full_token, hash)
}

/// Generate a password reset token. Returns `(full_token, sha256_hash)`.
/// Format: `kura_rst_` + 32 random bytes hex-encoded.
pub fn generate_password_reset_token() -> (String, String) {
    let raw = random_hex(32);
    let full_token = format!("kura_rst_{raw}");
    let hash = hash_token(&full_token);
    (full_token, hash)
}

/// Generate an authorization code. Returns `(code, sha256_hash)`.
/// 32 random bytes hex-encoded (no prefix).
pub fn generate_auth_code() -> (String, String) {
    let code = random_hex(32);
    let hash = hash_token(&code);
    (code, hash)
}

/// SHA-256 hex digest of a token string.
pub fn hash_token(token: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(token.as_bytes());
    hex::encode(hasher.finalize())
}

/// Extract the first 8 chars after `kura_sk_` for display/identification.
pub fn key_prefix(full_key: &str) -> String {
    full_key
        .strip_prefix("kura_sk_")
        .map(|rest| rest.chars().take(8).collect())
        .unwrap_or_default()
}

/// Hash a password with Argon2id and a random salt.
pub fn hash_password(password: &str) -> Result<String, String> {
    let salt = SaltString::generate(&mut OsRng);
    let argon2 = Argon2::default();
    argon2
        .hash_password(password.as_bytes(), &salt)
        .map(|h| h.to_string())
        .map_err(|e| format!("Failed to hash password: {e}"))
}

/// Verify a password against an Argon2id hash.
pub fn verify_password(password: &str, hash: &str) -> Result<bool, String> {
    let parsed = PasswordHash::new(hash).map_err(|e| format!("Invalid password hash: {e}"))?;
    Ok(Argon2::default()
        .verify_password(password.as_bytes(), &parsed)
        .is_ok())
}

/// Generate a PKCE code verifier (43-128 random URL-safe characters).
pub fn generate_code_verifier() -> String {
    let bytes: Vec<u8> = (0..32).map(|_| rand::thread_rng().r#gen::<u8>()).collect();
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&bytes)
}

/// Generate a PKCE code challenge from a verifier: `BASE64URL_NO_PAD(SHA256(verifier))`.
pub fn generate_code_challenge(verifier: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(verifier.as_bytes());
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(hasher.finalize())
}

/// Verify a PKCE code verifier against a stored challenge.
pub fn verify_pkce(code_verifier: &str, stored_challenge: &str) -> bool {
    generate_code_challenge(code_verifier) == stored_challenge
}

/// Generate `n` random bytes and return as hex string.
fn random_hex(n: usize) -> String {
    let bytes: Vec<u8> = (0..n).map(|_| rand::thread_rng().r#gen::<u8>()).collect();
    hex::encode(&bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn api_key_roundtrip() {
        let (key, hash) = generate_api_key();
        assert!(key.starts_with("kura_sk_"));
        assert_eq!(hash, hash_token(&key));
        assert_eq!(key_prefix(&key).len(), 8);
    }

    #[test]
    fn access_token_roundtrip() {
        let (token, hash) = generate_access_token();
        assert!(token.starts_with("kura_at_"));
        assert_eq!(hash, hash_token(&token));
    }

    #[test]
    fn refresh_token_roundtrip() {
        let (token, hash) = generate_refresh_token();
        assert!(token.starts_with("kura_rt_"));
        assert_eq!(hash, hash_token(&token));
    }

    #[test]
    fn password_reset_token_roundtrip() {
        let (token, hash) = generate_password_reset_token();
        assert!(token.starts_with("kura_rst_"));
        assert_eq!(hash, hash_token(&token));
    }

    #[test]
    fn auth_code_roundtrip() {
        let (code, hash) = generate_auth_code();
        assert!(!code.is_empty());
        assert_eq!(hash, hash_token(&code));
    }

    #[test]
    fn password_roundtrip() {
        let password = "test_password_123";
        let hash = hash_password(password).unwrap();
        assert!(verify_password(password, &hash).unwrap());
        assert!(!verify_password("wrong_password", &hash).unwrap());
    }

    #[test]
    fn pkce_roundtrip() {
        let verifier = generate_code_verifier();
        let challenge = generate_code_challenge(&verifier);
        assert!(verify_pkce(&verifier, &challenge));
        assert!(!verify_pkce("wrong_verifier", &challenge));
    }
}
