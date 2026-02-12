use axum::extract::{Path, State};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use kura_core::error::ApiError;

use crate::auth::{AuthMethod, AuthenticatedUser};
use crate::error::AppError;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/providers/connections", get(list_provider_connections))
        .route(
            "/v1/providers/connections",
            post(upsert_provider_connection),
        )
        .route(
            "/v1/providers/connections/{connection_id}/revoke",
            post(revoke_provider_connection),
        )
}

#[derive(Debug, Deserialize, Serialize, utoipa::ToSchema)]
pub struct UpsertProviderConnectionRequest {
    pub provider: String,
    pub provider_account_id: String,
    pub auth_state: String,
    #[serde(default)]
    pub scopes: Vec<String>,
    #[serde(default)]
    pub consented_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub token_expires_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub sync_cursor: Option<String>,
    #[serde(default)]
    pub access_token_ref: Option<String>,
    #[serde(default)]
    pub refresh_token_ref: Option<String>,
    #[serde(default)]
    pub token_fingerprint: Option<String>,
    #[serde(default)]
    pub last_oauth_state_nonce: Option<String>,
    #[serde(default)]
    pub last_error_code: Option<String>,
    #[serde(default)]
    pub last_error_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Deserialize, Serialize, utoipa::ToSchema)]
pub struct RevokeProviderConnectionRequest {
    pub reason: String,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct ProviderConnectionAdapterContext {
    pub provider_user_id: String,
    pub ingestion_method: String,
    pub ready: bool,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct ProviderConnectionAuditMeta {
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub created_by: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub updated_by: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_oauth_state_nonce: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_error_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_error_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct ProviderConnectionResponse {
    pub id: Uuid,
    pub provider: String,
    pub provider_account_id: String,
    pub auth_state: String,
    pub scopes: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub consented_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token_expires_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token_rotated_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token_fingerprint: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sync_cursor: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_sync_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub revoked_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub revoked_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub revoked_by: Option<String>,
    pub adapter_context: ProviderConnectionAdapterContext,
    pub audit: ProviderConnectionAuditMeta,
}

#[derive(Debug, sqlx::FromRow)]
struct ProviderConnectionRow {
    id: Uuid,
    provider: String,
    provider_account_id: String,
    auth_state: String,
    scopes: Vec<String>,
    consented_at: Option<DateTime<Utc>>,
    token_expires_at: Option<DateTime<Utc>>,
    token_rotated_at: Option<DateTime<Utc>>,
    token_fingerprint: Option<String>,
    sync_cursor: Option<String>,
    last_sync_at: Option<DateTime<Utc>>,
    revoked_at: Option<DateTime<Utc>>,
    revoked_reason: Option<String>,
    revoked_by: Option<String>,
    created_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
    created_by: Option<String>,
    updated_by: Option<String>,
    last_oauth_state_nonce: Option<String>,
    last_error_code: Option<String>,
    last_error_at: Option<DateTime<Utc>>,
}

impl ProviderConnectionRow {
    fn into_response(self) -> ProviderConnectionResponse {
        let ready = self.auth_state == "linked" && self.revoked_at.is_none();
        ProviderConnectionResponse {
            id: self.id,
            provider: self.provider.clone(),
            provider_account_id: self.provider_account_id.clone(),
            auth_state: self.auth_state.clone(),
            scopes: self.scopes,
            consented_at: self.consented_at,
            token_expires_at: self.token_expires_at,
            token_rotated_at: self.token_rotated_at,
            token_fingerprint: self.token_fingerprint,
            sync_cursor: self.sync_cursor,
            last_sync_at: self.last_sync_at,
            revoked_at: self.revoked_at,
            revoked_reason: self.revoked_reason,
            revoked_by: self.revoked_by,
            adapter_context: ProviderConnectionAdapterContext {
                provider_user_id: self.provider_account_id,
                ingestion_method: "connector_api".to_string(),
                ready,
            },
            audit: ProviderConnectionAuditMeta {
                created_at: self.created_at,
                updated_at: self.updated_at,
                created_by: self.created_by,
                updated_by: self.updated_by,
                last_oauth_state_nonce: self.last_oauth_state_nonce,
                last_error_code: self.last_error_code,
                last_error_at: self.last_error_at,
            },
        }
    }
}

fn normalize_non_empty(value: &str, field: &str) -> Result<String, AppError> {
    let normalized = value.trim().to_lowercase();
    if normalized.is_empty() {
        return Err(AppError::Validation {
            message: format!("{field} must not be empty"),
            field: Some(field.to_string()),
            received: Some(serde_json::Value::String(value.to_string())),
            docs_hint: None,
        });
    }
    Ok(normalized)
}

fn validate_provider(provider: &str) -> Result<String, AppError> {
    let normalized = normalize_non_empty(provider, "provider")?;
    match normalized.as_str() {
        "garmin" | "strava" | "trainingpeaks" => Ok(normalized),
        _ => Err(AppError::Validation {
            message: "provider must be one of garmin, strava, trainingpeaks".to_string(),
            field: Some("provider".to_string()),
            received: Some(serde_json::Value::String(provider.to_string())),
            docs_hint: Some("Connector provider must match the supported matrix.".to_string()),
        }),
    }
}

fn validate_auth_state_for_upsert(value: &str) -> Result<String, AppError> {
    let normalized = normalize_non_empty(value, "auth_state")?;
    match normalized.as_str() {
        "linked" | "refresh_required" | "error" => Ok(normalized),
        _ => Err(AppError::Validation {
            message: "auth_state must be linked, refresh_required, or error".to_string(),
            field: Some("auth_state".to_string()),
            received: Some(serde_json::Value::String(value.to_string())),
            docs_hint: Some("Use revoke endpoint for revoked state transitions.".to_string()),
        }),
    }
}

fn normalize_scopes(scopes: Vec<String>) -> Vec<String> {
    let mut normalized: Vec<String> = scopes
        .into_iter()
        .map(|scope| scope.trim().to_lowercase())
        .filter(|scope| !scope.is_empty())
        .collect();
    normalized.sort();
    normalized.dedup();
    normalized
}

fn actor_label(auth: &AuthenticatedUser) -> String {
    match &auth.auth_method {
        AuthMethod::ApiKey { key_id } => format!("api_key:{key_id}"),
        AuthMethod::AccessToken {
            token_id,
            client_id,
        } => format!("access_token:{client_id}:{token_id}"),
    }
}

/// List all provider connections for the authenticated user.
#[utoipa::path(
    get,
    path = "/v1/providers/connections",
    responses(
        (status = 200, description = "Provider connections", body = Vec<ProviderConnectionResponse>),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "providers"
)]
pub async fn list_provider_connections(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
) -> Result<Json<Vec<ProviderConnectionResponse>>, AppError> {
    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(auth.user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let rows = sqlx::query_as::<_, ProviderConnectionRow>(
        r#"
        SELECT
            id, provider, provider_account_id, auth_state, scopes,
            consented_at, token_expires_at, token_rotated_at, token_fingerprint,
            sync_cursor, last_sync_at,
            revoked_at, revoked_reason, revoked_by,
            created_at, updated_at, created_by, updated_by,
            last_oauth_state_nonce, last_error_code, last_error_at
        FROM provider_connections
        WHERE user_id = $1
        ORDER BY updated_at DESC, id DESC
        "#,
    )
    .bind(auth.user_id)
    .fetch_all(&mut *tx)
    .await?;

    tx.commit().await?;
    Ok(Json(
        rows.into_iter()
            .map(ProviderConnectionRow::into_response)
            .collect(),
    ))
}

/// Upsert provider connection metadata (post-launch connector domain model).
#[utoipa::path(
    post,
    path = "/v1/providers/connections",
    request_body = UpsertProviderConnectionRequest,
    responses(
        (status = 200, description = "Provider connection upserted", body = ProviderConnectionResponse),
        (status = 400, description = "Validation failed", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "providers"
)]
pub async fn upsert_provider_connection(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<UpsertProviderConnectionRequest>,
) -> Result<Json<ProviderConnectionResponse>, AppError> {
    let provider = validate_provider(&req.provider)?;
    let provider_account_id = normalize_non_empty(&req.provider_account_id, "provider_account_id")?;
    let auth_state = validate_auth_state_for_upsert(&req.auth_state)?;
    let scopes = normalize_scopes(req.scopes);
    let actor = actor_label(&auth);

    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(auth.user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let row = sqlx::query_as::<_, ProviderConnectionRow>(
        r#"
        INSERT INTO provider_connections (
            user_id,
            provider,
            provider_account_id,
            auth_state,
            scopes,
            consented_at,
            token_expires_at,
            token_rotated_at,
            access_token_ref,
            refresh_token_ref,
            token_fingerprint,
            sync_cursor,
            last_oauth_state_nonce,
            last_error_code,
            last_error_at,
            revoked_at,
            revoked_reason,
            revoked_by,
            created_by,
            updated_by,
            updated_at
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, NOW(), $8, $9, $10, $11, $12, $13, $14,
            NULL, NULL, NULL,
            $15, $15, NOW()
        )
        ON CONFLICT (user_id, provider, provider_account_id)
        DO UPDATE SET
            auth_state = EXCLUDED.auth_state,
            scopes = EXCLUDED.scopes,
            consented_at = COALESCE(EXCLUDED.consented_at, provider_connections.consented_at),
            token_expires_at = EXCLUDED.token_expires_at,
            token_rotated_at = EXCLUDED.token_rotated_at,
            access_token_ref = EXCLUDED.access_token_ref,
            refresh_token_ref = EXCLUDED.refresh_token_ref,
            token_fingerprint = EXCLUDED.token_fingerprint,
            sync_cursor = EXCLUDED.sync_cursor,
            last_oauth_state_nonce = EXCLUDED.last_oauth_state_nonce,
            last_error_code = EXCLUDED.last_error_code,
            last_error_at = EXCLUDED.last_error_at,
            revoked_at = NULL,
            revoked_reason = NULL,
            revoked_by = NULL,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
        RETURNING
            id, provider, provider_account_id, auth_state, scopes,
            consented_at, token_expires_at, token_rotated_at, token_fingerprint,
            sync_cursor, last_sync_at,
            revoked_at, revoked_reason, revoked_by,
            created_at, updated_at, created_by, updated_by,
            last_oauth_state_nonce, last_error_code, last_error_at
        "#,
    )
    .bind(auth.user_id)
    .bind(&provider)
    .bind(&provider_account_id)
    .bind(&auth_state)
    .bind(scopes)
    .bind(req.consented_at)
    .bind(req.token_expires_at)
    .bind(req.access_token_ref.as_deref())
    .bind(req.refresh_token_ref.as_deref())
    .bind(req.token_fingerprint.as_deref())
    .bind(req.sync_cursor.as_deref())
    .bind(req.last_oauth_state_nonce.as_deref())
    .bind(req.last_error_code.as_deref())
    .bind(req.last_error_at)
    .bind(&actor)
    .fetch_one(&mut *tx)
    .await?;

    tx.commit().await?;
    Ok(Json(row.into_response()))
}

/// Revoke an active provider connection.
#[utoipa::path(
    post,
    path = "/v1/providers/connections/{connection_id}/revoke",
    params(
        ("connection_id" = Uuid, Path, description = "Provider connection id")
    ),
    request_body = RevokeProviderConnectionRequest,
    responses(
        (status = 200, description = "Provider connection revoked", body = ProviderConnectionResponse),
        (status = 400, description = "Validation failed", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 404, description = "Connection not found", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "providers"
)]
pub async fn revoke_provider_connection(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path(connection_id): Path<Uuid>,
    Json(req): Json<RevokeProviderConnectionRequest>,
) -> Result<Json<ProviderConnectionResponse>, AppError> {
    let reason = req.reason.trim();
    if reason.is_empty() {
        return Err(AppError::Validation {
            message: "reason must not be empty".to_string(),
            field: Some("reason".to_string()),
            received: None,
            docs_hint: Some("Provide a short revocation reason for auditability.".to_string()),
        });
    }
    let actor = actor_label(&auth);

    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(auth.user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let row = sqlx::query_as::<_, ProviderConnectionRow>(
        r#"
        UPDATE provider_connections
        SET auth_state = 'revoked',
            revoked_at = NOW(),
            revoked_reason = $3,
            revoked_by = $4,
            access_token_ref = NULL,
            refresh_token_ref = NULL,
            token_expires_at = NULL,
            last_error_code = NULL,
            last_error_at = NULL,
            updated_by = $4,
            updated_at = NOW()
        WHERE id = $1
          AND user_id = $2
        RETURNING
            id, provider, provider_account_id, auth_state, scopes,
            consented_at, token_expires_at, token_rotated_at, token_fingerprint,
            sync_cursor, last_sync_at,
            revoked_at, revoked_reason, revoked_by,
            created_at, updated_at, created_by, updated_by,
            last_oauth_state_nonce, last_error_code, last_error_at
        "#,
    )
    .bind(connection_id)
    .bind(auth.user_id)
    .bind(reason)
    .bind(&actor)
    .fetch_optional(&mut *tx)
    .await?;

    tx.commit().await?;

    match row {
        Some(connection) => Ok(Json(connection.into_response())),
        None => Err(AppError::NotFound {
            resource: format!("provider connection {}", connection_id),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::{AppError, normalize_scopes, validate_auth_state_for_upsert, validate_provider};

    #[test]
    fn provider_validation_accepts_supported_values() {
        assert_eq!(validate_provider("garmin").unwrap(), "garmin");
        assert_eq!(validate_provider("STRAVA").unwrap(), "strava");
        assert_eq!(validate_provider("trainingpeaks").unwrap(), "trainingpeaks");
    }

    #[test]
    fn provider_validation_rejects_unknown_values() {
        let err = validate_provider("polar").expect_err("unsupported provider should fail");
        match err {
            AppError::Validation { field, .. } => assert_eq!(field.as_deref(), Some("provider")),
            other => panic!("unexpected error: {:?}", other),
        }
    }

    #[test]
    fn auth_state_validation_restricts_to_non_revoked_upsert_states() {
        assert_eq!(validate_auth_state_for_upsert("linked").unwrap(), "linked");
        assert_eq!(
            validate_auth_state_for_upsert("refresh_required").unwrap(),
            "refresh_required"
        );
        assert_eq!(validate_auth_state_for_upsert("error").unwrap(), "error");
        assert!(validate_auth_state_for_upsert("revoked").is_err());
    }

    #[test]
    fn normalize_scopes_trims_dedups_and_sorts() {
        let result = normalize_scopes(vec![
            " activity:read ".to_string(),
            "profile:read".to_string(),
            "activity:read".to_string(),
        ]);
        assert_eq!(
            result,
            vec!["activity:read".to_string(), "profile:read".to_string()]
        );
    }
}
