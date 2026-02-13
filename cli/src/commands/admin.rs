use clap::Subcommand;
use serde_json::json;

use crate::util::{api_request, exit_error};

#[derive(Subcommand)]
pub enum AdminCommands {
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
    /// Permanently delete a user and all their data (admin only, via API)
    DeleteUser {
        /// User UUID to delete
        #[arg(long)]
        user_id: String,
        /// Confirm deletion (required)
        #[arg(long)]
        confirm: bool,
    },
    /// List access requests (admin only, via API)
    ListRequests {
        /// Filter by status: pending, approved, rejected
        #[arg(long, default_value = "pending")]
        status: String,
    },
    /// Approve an access request and generate invite token (admin only, via API)
    ApproveRequest {
        /// Access request UUID
        id: String,
    },
    /// Reject an access request (admin only, via API)
    RejectRequest {
        /// Access request UUID
        id: String,
    },
    /// Create a manual invite token (admin only, via API)
    CreateInvite {
        /// Bind to specific email (optional)
        #[arg(long)]
        email: Option<String>,
        /// Expiration in days (default: 7)
        #[arg(long, default_value = "7")]
        expires_in_days: i64,
    },
    /// List invite tokens (admin only, via API)
    ListInvites {
        /// Filter: unused, used, expired
        #[arg(long)]
        status: Option<String>,
    },
    /// Audited break-glass identity lookup by user UUID (admin only)
    SupportReidentify {
        /// Target user UUID
        #[arg(long)]
        user_id: String,
        /// Mandatory operational reason
        #[arg(long)]
        reason: String,
        /// Incident/support ticket reference
        #[arg(long)]
        ticket_id: String,
        /// Requested mode (identity_lookup|incident_debug)
        #[arg(long)]
        requested_mode: Option<String>,
        /// Optional RFC3339 expiry timestamp for this access grant
        #[arg(long)]
        expires_at: Option<String>,
    },
}

pub async fn run(api_url: &str, command: AdminCommands) -> i32 {
    match command {
        AdminCommands::CreateUser {
            email,
            password,
            display_name,
        } => create_user(&email, &password, display_name.as_deref()).await,
        AdminCommands::CreateKey {
            user_id,
            label,
            expires_in_days,
        } => create_key(&user_id, &label, expires_in_days).await,
        AdminCommands::DeleteUser { user_id, confirm } => {
            delete_user(api_url, &user_id, confirm).await
        }
        AdminCommands::ListRequests { status } => {
            let query = vec![("status".to_string(), status)];
            api_request(
                api_url,
                reqwest::Method::GET,
                "/v1/admin/access-requests",
                None,
                None,
                &query,
                &[],
                false,
                false,
            )
            .await
        }
        AdminCommands::ApproveRequest { id } => {
            api_request(
                api_url,
                reqwest::Method::POST,
                &format!("/v1/admin/access-requests/{id}/approve"),
                None,
                None,
                &[],
                &[],
                false,
                false,
            )
            .await
        }
        AdminCommands::RejectRequest { id } => {
            api_request(
                api_url,
                reqwest::Method::POST,
                &format!("/v1/admin/access-requests/{id}/reject"),
                None,
                None,
                &[],
                &[],
                false,
                false,
            )
            .await
        }
        AdminCommands::CreateInvite {
            email,
            expires_in_days,
        } => {
            let body = json!({
                "email": email,
                "expires_in_days": expires_in_days
            });
            api_request(
                api_url,
                reqwest::Method::POST,
                "/v1/admin/invites",
                None,
                Some(body),
                &[],
                &[],
                false,
                false,
            )
            .await
        }
        AdminCommands::ListInvites { status } => {
            let query: Vec<(String, String)> = status
                .map(|s| vec![("status".to_string(), s)])
                .unwrap_or_default();
            api_request(
                api_url,
                reqwest::Method::GET,
                "/v1/admin/invites",
                None,
                None,
                &query,
                &[],
                false,
                false,
            )
            .await
        }
        AdminCommands::SupportReidentify {
            user_id,
            reason,
            ticket_id,
            requested_mode,
            expires_at,
        } => {
            support_reidentify(
                api_url,
                &user_id,
                &reason,
                &ticket_id,
                requested_mode.as_deref(),
                expires_at.as_deref(),
            )
            .await
        }
    }
}

async fn create_user(email: &str, password: &str, display_name: Option<&str>) -> i32 {
    let database_url = match std::env::var("DATABASE_URL") {
        Ok(url) => url,
        Err(_) => exit_error(
            "DATABASE_URL must be set for admin commands",
            Some("Admin create commands connect directly to the database for bootstrapping"),
        ),
    };

    let pool = match sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&database_url)
        .await
    {
        Ok(p) => p,
        Err(e) => exit_error(&format!("Failed to connect to database: {e}"), None),
    };

    let password_hash = match kura_core::auth::hash_password(password) {
        Ok(h) => h,
        Err(e) => exit_error(&format!("Failed to hash password: {e}"), None),
    };

    let user_id = uuid::Uuid::now_v7();
    let email_norm = email.trim().to_lowercase();

    let mut tx = match pool.begin().await {
        Ok(tx) => tx,
        Err(e) => exit_error(&format!("Failed to start transaction: {e}"), None),
    };

    if let Err(e) = sqlx::query(
        "INSERT INTO users (id, email, password_hash, display_name) VALUES ($1, $2, $3, $4)",
    )
    .bind(user_id)
    .bind(&email_norm)
    .bind(&password_hash)
    .bind(display_name)
    .execute(&mut *tx)
    .await
    {
        exit_error(&format!("Failed to create user: {e}"), None);
    }

    if let Err(e) = sqlx::query(
        "INSERT INTO user_identities \
         (user_id, provider, provider_subject, email_norm, email_verified_at) \
         VALUES ($1, 'email_password', $2, $2, NOW())",
    )
    .bind(user_id)
    .bind(&email_norm)
    .execute(&mut *tx)
    .await
    {
        exit_error(&format!("Failed to create user identity: {e}"), None);
    }

    if let Err(e) = sqlx::query(
        "INSERT INTO analysis_subjects (user_id, analysis_subject_id) \
         VALUES ($1, 'asub_' || replace(gen_random_uuid()::text, '-', '')) \
         ON CONFLICT (user_id) DO NOTHING",
    )
    .bind(user_id)
    .execute(&mut *tx)
    .await
    {
        exit_error(&format!("Failed to create analysis subject: {e}"), None);
    }

    if let Err(e) = tx.commit().await {
        exit_error(&format!("Failed to commit user creation: {e}"), None);
    }

    let output = json!({
        "user_id": user_id,
        "email": email_norm,
        "display_name": display_name
    });
    println!("{}", serde_json::to_string_pretty(&output).unwrap());
    0
}

async fn create_key(user_id_str: &str, label: &str, expires_in_days: Option<i64>) -> i32 {
    let database_url = match std::env::var("DATABASE_URL") {
        Ok(url) => url,
        Err(_) => exit_error(
            "DATABASE_URL must be set for admin commands",
            Some("Admin create commands connect directly to the database for bootstrapping"),
        ),
    };

    let pool = match sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&database_url)
        .await
    {
        Ok(p) => p,
        Err(e) => exit_error(&format!("Failed to connect to database: {e}"), None),
    };

    let user_id = match uuid::Uuid::parse_str(user_id_str) {
        Ok(u) => u,
        Err(e) => exit_error(&format!("Invalid user UUID: {e}"), None),
    };

    let (full_key, key_hash) = kura_core::auth::generate_api_key();
    let prefix = kura_core::auth::key_prefix(&full_key);
    let key_id = uuid::Uuid::now_v7();
    let scopes = vec![
        "agent:read".to_string(),
        "agent:write".to_string(),
        "agent:resolve".to_string(),
    ];

    let expires_at = expires_in_days.map(|d| chrono::Utc::now() + chrono::Duration::days(d));

    if let Err(e) = sqlx::query(
        "INSERT INTO api_keys (id, user_id, key_hash, key_prefix, label, scopes, expires_at) \
         VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(key_id)
    .bind(user_id)
    .bind(&key_hash)
    .bind(&prefix)
    .bind(label)
    .bind(scopes.clone())
    .bind(expires_at)
    .execute(&pool)
    .await
    {
        exit_error(&format!("Failed to create API key: {e}"), None);
    }

    let output = json!({
        "key_id": key_id,
        "api_key": full_key,
        "key_prefix": prefix,
        "label": label,
        "scopes": scopes,
        "expires_at": expires_at,
        "warning": "Store this key securely. It will NOT be shown again."
    });
    println!("{}", serde_json::to_string_pretty(&output).unwrap());
    0
}

async fn delete_user(api_url: &str, user_id: &str, confirm: bool) -> i32 {
    if !confirm {
        exit_error(
            "Account deletion is permanent and irreversible",
            Some("Add --confirm to proceed: kura admin delete-user --user-id <UUID> --confirm"),
        );
    }

    let token = match crate::util::resolve_token(api_url).await {
        Ok(t) => t,
        Err(e) => exit_error(
            &e.to_string(),
            Some("Run `kura login` or set KURA_API_KEY (admin credentials required)"),
        ),
    };

    api_request(
        api_url,
        reqwest::Method::DELETE,
        &format!("/v1/admin/users/{user_id}"),
        Some(&token),
        None,
        &[],
        &[],
        false,
        false,
    )
    .await
}

async fn support_reidentify(
    api_url: &str,
    user_id: &str,
    reason: &str,
    ticket_id: &str,
    requested_mode: Option<&str>,
    expires_at: Option<&str>,
) -> i32 {
    let token = match crate::util::resolve_token(api_url).await {
        Ok(t) => t,
        Err(e) => exit_error(
            &e.to_string(),
            Some("Run `kura login` or set KURA_API_KEY (admin credentials required)"),
        ),
    };

    let user_id = match uuid::Uuid::parse_str(user_id) {
        Ok(v) => v,
        Err(e) => exit_error(&format!("Invalid user UUID: {e}"), None),
    };

    let mut body = json!({
        "user_id": user_id,
        "reason": reason,
        "ticket_id": ticket_id,
    });
    if let Some(mode) = requested_mode {
        body["requested_mode"] = json!(mode);
    }
    if let Some(expires) = expires_at {
        body["expires_at"] = json!(expires);
    }

    api_request(
        api_url,
        reqwest::Method::POST,
        "/v1/admin/support/reidentify",
        Some(&token),
        Some(body),
        &[],
        &[],
        false,
        false,
    )
    .await
}
