use axum::extract::{Path, Query, State};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::middleware::kill_switch::{fetch_kill_switch_status, persist_kill_switch_audit_event};
use crate::security_profile::{SecurityProfile, SecurityProfileRolloutConfig, load_rollout_config};
use crate::state::AppState;

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct KillSwitchStatusResponse {
    pub is_active: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub activated_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub activated_by: Option<Uuid>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct ActivateKillSwitchRequest {
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct DeactivateKillSwitchRequest {
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct KillSwitchAuditQuery {
    #[serde(default)]
    pub limit: Option<i64>,
}

#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct SecurityAbuseTelemetryQuery {
    #[serde(default)]
    pub limit: Option<i64>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct KillSwitchAuditEvent {
    pub id: i64,
    pub timestamp: DateTime<Utc>,
    pub action: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub actor_user_id: Option<Uuid>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_user_id: Option<Uuid>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub method: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    pub metadata: Value,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct SecurityAbuseTelemetryEvent {
    pub id: i64,
    pub timestamp: DateTime<Utc>,
    pub user_id: Uuid,
    pub path: String,
    pub method: String,
    pub action: String,
    pub risk_score: i32,
    pub cooldown_active: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cooldown_until: Option<DateTime<Utc>>,
    pub total_requests_60s: i32,
    pub denied_requests_60s: i32,
    pub unique_paths_60s: i32,
    pub context_reads_60s: i32,
    pub denied_ratio_60s: f64,
    pub signals: Vec<String>,
    pub false_positive_hint: bool,
    pub ux_impact_hint: String,
    pub response_status_code: i16,
    pub response_time_ms: i32,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct UpdateSecurityProfileRolloutRequest {
    pub default_profile: SecurityProfile,
    pub adaptive_rollout_percent: i16,
    pub strict_rollout_percent: i16,
    #[serde(default)]
    pub notes: Option<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct SecurityProfileRolloutStatusResponse {
    pub config: SecurityProfileRolloutConfig,
    pub override_count: i64,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct UpsertSecurityProfileOverrideRequest {
    pub profile: SecurityProfile,
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct SecurityProfileOverrideResponse {
    pub user_id: Uuid,
    pub profile: SecurityProfile,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    pub updated_at: DateTime<Utc>,
    pub updated_by: Uuid,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct DeleteSecurityProfileOverrideResponse {
    pub user_id: Uuid,
    pub removed: bool,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct RecordRolloutDecisionRequest {
    pub decision: String,
    pub rationale: String,
    #[serde(default)]
    pub metrics_snapshot: Value,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct RolloutDecisionRecord {
    pub id: i64,
    pub timestamp: DateTime<Utc>,
    pub actor_user_id: Uuid,
    pub decision: String,
    pub rationale: String,
    pub metrics_snapshot: Value,
    pub rollout_config: Value,
}

#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct RolloutDecisionQuery {
    #[serde(default)]
    pub limit: Option<i64>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct SecurityGuardrailProfileMetrics {
    pub profile: SecurityProfile,
    pub sampled_requests: i64,
    pub blocked_requests: i64,
    pub throttled_requests: i64,
    pub false_positive_hints: i64,
    pub avg_response_time_ms: f64,
    pub success_rate: f64,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct SecurityGuardrailDashboardResponse {
    pub generated_at: DateTime<Utc>,
    pub rollout: SecurityProfileRolloutConfig,
    pub kill_switch_active: bool,
    pub profile_metrics: Vec<SecurityGuardrailProfileMetrics>,
}

#[derive(sqlx::FromRow)]
struct KillSwitchAuditEventRow {
    id: i64,
    timestamp: DateTime<Utc>,
    action: String,
    actor_user_id: Option<Uuid>,
    target_user_id: Option<Uuid>,
    path: Option<String>,
    method: Option<String>,
    reason: Option<String>,
    metadata: Value,
}

#[derive(sqlx::FromRow)]
struct SecurityAbuseTelemetryRow {
    id: i64,
    timestamp: DateTime<Utc>,
    user_id: Uuid,
    path: String,
    method: String,
    action: String,
    risk_score: i32,
    cooldown_active: bool,
    cooldown_until: Option<DateTime<Utc>>,
    total_requests_60s: i32,
    denied_requests_60s: i32,
    unique_paths_60s: i32,
    context_reads_60s: i32,
    denied_ratio_60s: f64,
    signals: Vec<String>,
    false_positive_hint: bool,
    ux_impact_hint: String,
    response_status_code: i16,
    response_time_ms: i32,
}

#[derive(sqlx::FromRow)]
struct SecurityProfileOverrideRow {
    user_id: Uuid,
    profile: String,
    reason: Option<String>,
    updated_at: DateTime<Utc>,
    updated_by: Uuid,
}

#[derive(sqlx::FromRow)]
struct RolloutDecisionRow {
    id: i64,
    timestamp: DateTime<Utc>,
    actor_user_id: Uuid,
    decision: String,
    rationale: String,
    metrics_snapshot: Value,
    rollout_config: Value,
}

#[derive(sqlx::FromRow)]
struct GuardrailMetricsRow {
    profile: String,
    sampled_requests: i64,
    blocked_requests: i64,
    throttled_requests: i64,
    false_positive_hints: i64,
    avg_response_time_ms: Option<f64>,
    success_rate: Option<f64>,
}

impl From<KillSwitchAuditEventRow> for KillSwitchAuditEvent {
    fn from(row: KillSwitchAuditEventRow) -> Self {
        Self {
            id: row.id,
            timestamp: row.timestamp,
            action: row.action,
            actor_user_id: row.actor_user_id,
            target_user_id: row.target_user_id,
            path: row.path,
            method: row.method,
            reason: row.reason,
            metadata: row.metadata,
        }
    }
}

impl From<SecurityAbuseTelemetryRow> for SecurityAbuseTelemetryEvent {
    fn from(row: SecurityAbuseTelemetryRow) -> Self {
        Self {
            id: row.id,
            timestamp: row.timestamp,
            user_id: row.user_id,
            path: row.path,
            method: row.method,
            action: row.action,
            risk_score: row.risk_score,
            cooldown_active: row.cooldown_active,
            cooldown_until: row.cooldown_until,
            total_requests_60s: row.total_requests_60s,
            denied_requests_60s: row.denied_requests_60s,
            unique_paths_60s: row.unique_paths_60s,
            context_reads_60s: row.context_reads_60s,
            denied_ratio_60s: row.denied_ratio_60s,
            signals: row.signals,
            false_positive_hint: row.false_positive_hint,
            ux_impact_hint: row.ux_impact_hint,
            response_status_code: row.response_status_code,
            response_time_ms: row.response_time_ms,
        }
    }
}

impl From<SecurityProfileOverrideRow> for SecurityProfileOverrideResponse {
    fn from(row: SecurityProfileOverrideRow) -> Self {
        Self {
            user_id: row.user_id,
            profile: SecurityProfile::from_db_value(&row.profile),
            reason: row.reason,
            updated_at: row.updated_at,
            updated_by: row.updated_by,
        }
    }
}

impl From<RolloutDecisionRow> for RolloutDecisionRecord {
    fn from(row: RolloutDecisionRow) -> Self {
        Self {
            id: row.id,
            timestamp: row.timestamp,
            actor_user_id: row.actor_user_id,
            decision: row.decision,
            rationale: row.rationale,
            metrics_snapshot: row.metrics_snapshot,
            rollout_config: row.rollout_config,
        }
    }
}

fn normalize_reason(reason: Option<String>, fallback: &str) -> String {
    reason
        .and_then(|value| {
            let trimmed = value.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
            }
        })
        .unwrap_or_else(|| fallback.to_string())
}

fn normalize_optional_text(value: Option<String>) -> Option<String> {
    value.and_then(|text| {
        let trimmed = text.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn validate_rollout_config_request(
    req: &UpdateSecurityProfileRolloutRequest,
) -> Result<(), AppError> {
    if !(0..=100).contains(&req.adaptive_rollout_percent) {
        return Err(AppError::Validation {
            message: "adaptive_rollout_percent must be between 0 and 100".to_string(),
            field: Some("adaptive_rollout_percent".to_string()),
            received: Some(serde_json::json!(req.adaptive_rollout_percent)),
            docs_hint: Some("Use a value in [0, 100].".to_string()),
        });
    }
    if !(0..=100).contains(&req.strict_rollout_percent) {
        return Err(AppError::Validation {
            message: "strict_rollout_percent must be between 0 and 100".to_string(),
            field: Some("strict_rollout_percent".to_string()),
            received: Some(serde_json::json!(req.strict_rollout_percent)),
            docs_hint: Some("Use a value in [0, 100].".to_string()),
        });
    }
    if req.adaptive_rollout_percent + req.strict_rollout_percent > 100 {
        return Err(AppError::Validation {
            message: "adaptive + strict rollout percent must not exceed 100".to_string(),
            field: Some("adaptive_rollout_percent+strict_rollout_percent".to_string()),
            received: Some(serde_json::json!({
                "adaptive_rollout_percent": req.adaptive_rollout_percent,
                "strict_rollout_percent": req.strict_rollout_percent
            })),
            docs_hint: Some(
                "Reduce one rollout percentage so combined value is <= 100.".to_string(),
            ),
        });
    }
    Ok(())
}

async fn ensure_admin(state: &AppState, auth: &AuthenticatedUser) -> Result<(), AppError> {
    let is_admin: bool = sqlx::query_scalar("SELECT is_admin FROM users WHERE id = $1")
        .bind(auth.user_id)
        .fetch_one(&state.db)
        .await
        .map_err(AppError::Database)?;
    if is_admin {
        Ok(())
    } else {
        Err(AppError::Forbidden {
            message: "Admin privileges required".to_string(),
            docs_hint: Some("Only admin users can manage the security kill switch.".to_string()),
        })
    }
}

#[utoipa::path(
    get,
    path = "/v1/admin/security/kill-switch",
    responses(
        (status = 200, description = "Current kill-switch status", body = KillSwitchStatusResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_kill_switch_status(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
) -> Result<Json<KillSwitchStatusResponse>, AppError> {
    ensure_admin(&state, &auth).await?;
    let status = fetch_kill_switch_status(&state.db)
        .await
        .map_err(AppError::Database)?;
    Ok(Json(KillSwitchStatusResponse {
        is_active: status.is_active,
        reason: status.reason,
        activated_at: status.activated_at,
        activated_by: status.activated_by,
        updated_at: status.updated_at,
    }))
}

#[utoipa::path(
    post,
    path = "/v1/admin/security/kill-switch/activate",
    request_body = ActivateKillSwitchRequest,
    responses(
        (status = 200, description = "Kill switch activated", body = KillSwitchStatusResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn activate_kill_switch(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<ActivateKillSwitchRequest>,
) -> Result<Json<KillSwitchStatusResponse>, AppError> {
    ensure_admin(&state, &auth).await?;
    let reason = normalize_reason(req.reason, "manual activation");

    sqlx::query(
        r#"
        UPDATE security_kill_switch_state
        SET is_active = TRUE,
            reason = $1,
            activated_at = NOW(),
            activated_by = $2,
            updated_at = NOW()
        WHERE id = TRUE
        "#,
    )
    .bind(&reason)
    .bind(auth.user_id)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    persist_kill_switch_audit_event(
        state.db.clone(),
        "activated",
        Some(auth.user_id),
        None,
        None,
        None,
        Some(reason.clone()),
        serde_json::json!({ "source": "admin_api" }),
    );

    let status = fetch_kill_switch_status(&state.db)
        .await
        .map_err(AppError::Database)?;
    Ok(Json(KillSwitchStatusResponse {
        is_active: status.is_active,
        reason: status.reason,
        activated_at: status.activated_at,
        activated_by: status.activated_by,
        updated_at: status.updated_at,
    }))
}

#[utoipa::path(
    post,
    path = "/v1/admin/security/kill-switch/deactivate",
    request_body = DeactivateKillSwitchRequest,
    responses(
        (status = 200, description = "Kill switch deactivated", body = KillSwitchStatusResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn deactivate_kill_switch(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<DeactivateKillSwitchRequest>,
) -> Result<Json<KillSwitchStatusResponse>, AppError> {
    ensure_admin(&state, &auth).await?;
    let reason = normalize_reason(req.reason, "manual deactivation");

    sqlx::query(
        r#"
        UPDATE security_kill_switch_state
        SET is_active = FALSE,
            reason = $1,
            activated_at = NULL,
            activated_by = NULL,
            updated_at = NOW()
        WHERE id = TRUE
        "#,
    )
    .bind(&reason)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    persist_kill_switch_audit_event(
        state.db.clone(),
        "deactivated",
        Some(auth.user_id),
        None,
        None,
        None,
        Some(reason.clone()),
        serde_json::json!({ "source": "admin_api" }),
    );

    let status = fetch_kill_switch_status(&state.db)
        .await
        .map_err(AppError::Database)?;
    Ok(Json(KillSwitchStatusResponse {
        is_active: status.is_active,
        reason: status.reason,
        activated_at: status.activated_at,
        activated_by: status.activated_by,
        updated_at: status.updated_at,
    }))
}

#[utoipa::path(
    get,
    path = "/v1/admin/security/kill-switch/audit",
    params(KillSwitchAuditQuery),
    responses(
        (status = 200, description = "Kill-switch audit events", body = [KillSwitchAuditEvent]),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn list_kill_switch_audit(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Query(query): Query<KillSwitchAuditQuery>,
) -> Result<Json<Vec<KillSwitchAuditEvent>>, AppError> {
    ensure_admin(&state, &auth).await?;
    let limit = query.limit.unwrap_or(50).clamp(1, 200);

    let rows = sqlx::query_as::<_, KillSwitchAuditEventRow>(
        r#"
        SELECT id, timestamp, action, actor_user_id, target_user_id, path, method, reason, metadata
        FROM security_kill_switch_audit
        ORDER BY timestamp DESC, id DESC
        LIMIT $1
        "#,
    )
    .bind(limit)
    .fetch_all(&state.db)
    .await
    .map_err(AppError::Database)?;

    Ok(Json(
        rows.into_iter().map(KillSwitchAuditEvent::from).collect(),
    ))
}

#[utoipa::path(
    get,
    path = "/v1/admin/security/telemetry/abuse",
    params(SecurityAbuseTelemetryQuery),
    responses(
        (status = 200, description = "Recent adaptive-abuse telemetry entries", body = [SecurityAbuseTelemetryEvent]),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn list_security_abuse_telemetry(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Query(query): Query<SecurityAbuseTelemetryQuery>,
) -> Result<Json<Vec<SecurityAbuseTelemetryEvent>>, AppError> {
    ensure_admin(&state, &auth).await?;
    let limit = query.limit.unwrap_or(100).clamp(1, 500);

    let rows = sqlx::query_as::<_, SecurityAbuseTelemetryRow>(
        r#"
        SELECT
            id,
            timestamp,
            user_id,
            path,
            method,
            action,
            risk_score,
            cooldown_active,
            cooldown_until,
            total_requests_60s,
            denied_requests_60s,
            unique_paths_60s,
            context_reads_60s,
            denied_ratio_60s,
            signals,
            false_positive_hint,
            ux_impact_hint,
            response_status_code,
            response_time_ms
        FROM security_abuse_telemetry
        ORDER BY timestamp DESC, id DESC
        LIMIT $1
        "#,
    )
    .bind(limit)
    .fetch_all(&state.db)
    .await
    .map_err(AppError::Database)?;

    Ok(Json(
        rows.into_iter()
            .map(SecurityAbuseTelemetryEvent::from)
            .collect(),
    ))
}

#[utoipa::path(
    get,
    path = "/v1/admin/security/profiles/rollout",
    responses(
        (status = 200, description = "Current security-profile rollout config", body = SecurityProfileRolloutStatusResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_security_profile_rollout(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
) -> Result<Json<SecurityProfileRolloutStatusResponse>, AppError> {
    ensure_admin(&state, &auth).await?;
    let config = load_rollout_config(&state.db)
        .await
        .map_err(AppError::Database)?;
    let override_count: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM security_profile_user_overrides")
            .fetch_one(&state.db)
            .await
            .map_err(AppError::Database)?;

    Ok(Json(SecurityProfileRolloutStatusResponse {
        config,
        override_count,
    }))
}

#[utoipa::path(
    post,
    path = "/v1/admin/security/profiles/rollout",
    request_body = UpdateSecurityProfileRolloutRequest,
    responses(
        (status = 200, description = "Rollout config updated", body = SecurityProfileRolloutStatusResponse),
        (status = 400, description = "Validation failed"),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn update_security_profile_rollout(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<UpdateSecurityProfileRolloutRequest>,
) -> Result<Json<SecurityProfileRolloutStatusResponse>, AppError> {
    ensure_admin(&state, &auth).await?;
    validate_rollout_config_request(&req)?;
    let notes = normalize_optional_text(req.notes);

    sqlx::query(
        r#"
        UPDATE security_profile_rollout
        SET default_profile = $1,
            adaptive_rollout_percent = $2,
            strict_rollout_percent = $3,
            notes = $4,
            updated_at = NOW(),
            updated_by = $5
        WHERE id = TRUE
        "#,
    )
    .bind(req.default_profile.as_str())
    .bind(req.adaptive_rollout_percent)
    .bind(req.strict_rollout_percent)
    .bind(notes.clone())
    .bind(auth.user_id)
    .execute(&state.db)
    .await
    .map_err(AppError::Database)?;

    persist_kill_switch_audit_event(
        state.db.clone(),
        "profile_rollout_updated",
        Some(auth.user_id),
        None,
        None,
        None,
        notes.clone(),
        serde_json::json!({
            "default_profile": req.default_profile.as_str(),
            "adaptive_rollout_percent": req.adaptive_rollout_percent,
            "strict_rollout_percent": req.strict_rollout_percent
        }),
    );

    get_security_profile_rollout(State(state), auth).await
}

#[utoipa::path(
    post,
    path = "/v1/admin/security/profiles/overrides/{user_id}",
    params(("user_id" = Uuid, Path, description = "User to override")),
    request_body = UpsertSecurityProfileOverrideRequest,
    responses(
        (status = 200, description = "Profile override upserted", body = SecurityProfileOverrideResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn upsert_security_profile_override(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path(user_id): Path<Uuid>,
    Json(req): Json<UpsertSecurityProfileOverrideRequest>,
) -> Result<Json<SecurityProfileOverrideResponse>, AppError> {
    ensure_admin(&state, &auth).await?;
    let reason = normalize_optional_text(req.reason);

    let row = sqlx::query_as::<_, SecurityProfileOverrideRow>(
        r#"
        INSERT INTO security_profile_user_overrides (user_id, profile, reason, updated_at, updated_by)
        VALUES ($1, $2, $3, NOW(), $4)
        ON CONFLICT (user_id) DO UPDATE
            SET profile = EXCLUDED.profile,
                reason = EXCLUDED.reason,
                updated_at = NOW(),
                updated_by = EXCLUDED.updated_by
        RETURNING user_id, profile, reason, updated_at, updated_by
        "#,
    )
    .bind(user_id)
    .bind(req.profile.as_str())
    .bind(reason.clone())
    .bind(auth.user_id)
    .fetch_one(&state.db)
    .await
    .map_err(AppError::Database)?;

    persist_kill_switch_audit_event(
        state.db.clone(),
        "profile_override_upserted",
        Some(auth.user_id),
        Some(user_id),
        None,
        None,
        reason,
        serde_json::json!({ "profile": req.profile.as_str() }),
    );

    Ok(Json(SecurityProfileOverrideResponse::from(row)))
}

#[utoipa::path(
    delete,
    path = "/v1/admin/security/profiles/overrides/{user_id}",
    params(("user_id" = Uuid, Path, description = "User override to remove")),
    responses(
        (status = 200, description = "Override removed", body = DeleteSecurityProfileOverrideResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn delete_security_profile_override(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path(user_id): Path<Uuid>,
) -> Result<Json<DeleteSecurityProfileOverrideResponse>, AppError> {
    ensure_admin(&state, &auth).await?;
    let result = sqlx::query("DELETE FROM security_profile_user_overrides WHERE user_id = $1")
        .bind(user_id)
        .execute(&state.db)
        .await
        .map_err(AppError::Database)?;
    let removed = result.rows_affected() > 0;

    persist_kill_switch_audit_event(
        state.db.clone(),
        "profile_override_deleted",
        Some(auth.user_id),
        Some(user_id),
        None,
        None,
        None,
        serde_json::json!({ "removed": removed }),
    );

    Ok(Json(DeleteSecurityProfileOverrideResponse {
        user_id,
        removed,
    }))
}

#[utoipa::path(
    post,
    path = "/v1/admin/security/profiles/decisions",
    request_body = RecordRolloutDecisionRequest,
    responses(
        (status = 200, description = "Rollout decision recorded", body = RolloutDecisionRecord),
        (status = 400, description = "Validation failed"),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn record_rollout_decision(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<RecordRolloutDecisionRequest>,
) -> Result<Json<RolloutDecisionRecord>, AppError> {
    ensure_admin(&state, &auth).await?;
    let decision = req.decision.trim().to_string();
    let rationale = req.rationale.trim().to_string();
    if decision.is_empty() || rationale.is_empty() {
        return Err(AppError::Validation {
            message: "decision and rationale must be non-empty".to_string(),
            field: Some("decision|rationale".to_string()),
            received: Some(serde_json::json!({
                "decision": req.decision,
                "rationale": req.rationale
            })),
            docs_hint: Some("Provide non-empty decision and rationale strings.".to_string()),
        });
    }

    let rollout = load_rollout_config(&state.db)
        .await
        .map_err(AppError::Database)?;
    let rollout_config = serde_json::json!({
        "default_profile": rollout.default_profile.as_str(),
        "adaptive_rollout_percent": rollout.adaptive_rollout_percent,
        "strict_rollout_percent": rollout.strict_rollout_percent,
        "updated_at": rollout.updated_at,
        "updated_by": rollout.updated_by,
        "notes": rollout.notes,
    });

    let row = sqlx::query_as::<_, RolloutDecisionRow>(
        r#"
        INSERT INTO security_profile_rollout_decisions (
            actor_user_id,
            decision,
            rationale,
            metrics_snapshot,
            rollout_config
        ) VALUES ($1, $2, $3, $4, $5)
        RETURNING id, timestamp, actor_user_id, decision, rationale, metrics_snapshot, rollout_config
        "#,
    )
    .bind(auth.user_id)
    .bind(decision)
    .bind(rationale)
    .bind(req.metrics_snapshot)
    .bind(rollout_config)
    .fetch_one(&state.db)
    .await
    .map_err(AppError::Database)?;

    Ok(Json(RolloutDecisionRecord::from(row)))
}

#[utoipa::path(
    get,
    path = "/v1/admin/security/profiles/decisions",
    params(RolloutDecisionQuery),
    responses(
        (status = 200, description = "Rollout decision records", body = [RolloutDecisionRecord]),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn list_rollout_decisions(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Query(query): Query<RolloutDecisionQuery>,
) -> Result<Json<Vec<RolloutDecisionRecord>>, AppError> {
    ensure_admin(&state, &auth).await?;
    let limit = query.limit.unwrap_or(50).clamp(1, 200);
    let rows = sqlx::query_as::<_, RolloutDecisionRow>(
        r#"
        SELECT id, timestamp, actor_user_id, decision, rationale, metrics_snapshot, rollout_config
        FROM security_profile_rollout_decisions
        ORDER BY timestamp DESC, id DESC
        LIMIT $1
        "#,
    )
    .bind(limit)
    .fetch_all(&state.db)
    .await
    .map_err(AppError::Database)?;

    Ok(Json(
        rows.into_iter().map(RolloutDecisionRecord::from).collect(),
    ))
}

#[utoipa::path(
    get,
    path = "/v1/admin/security/guardrails/dashboard",
    responses(
        (status = 200, description = "Security-vs-UX guardrail dashboard", body = SecurityGuardrailDashboardResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_security_guardrail_dashboard(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
) -> Result<Json<SecurityGuardrailDashboardResponse>, AppError> {
    ensure_admin(&state, &auth).await?;
    let rollout = load_rollout_config(&state.db)
        .await
        .map_err(AppError::Database)?;
    let kill_switch = fetch_kill_switch_status(&state.db)
        .await
        .map_err(AppError::Database)?;

    let rows = sqlx::query_as::<_, GuardrailMetricsRow>(
        r#"
        SELECT
            profile,
            COUNT(*) AS sampled_requests,
            COUNT(*) FILTER (WHERE action = 'block') AS blocked_requests,
            COUNT(*) FILTER (WHERE action = 'throttle') AS throttled_requests,
            COUNT(*) FILTER (WHERE false_positive_hint = TRUE) AS false_positive_hints,
            AVG(response_time_ms)::float8 AS avg_response_time_ms,
            AVG(CASE WHEN response_status_code >= 200 AND response_status_code < 300 THEN 1.0 ELSE 0.0 END)::float8 AS success_rate
        FROM security_abuse_telemetry
        WHERE timestamp >= NOW() - INTERVAL '24 hours'
        GROUP BY profile
        "#,
    )
    .fetch_all(&state.db)
    .await
    .map_err(AppError::Database)?;

    let mut by_profile = std::collections::HashMap::new();
    for row in rows {
        by_profile.insert(
            row.profile.clone(),
            SecurityGuardrailProfileMetrics {
                profile: SecurityProfile::from_db_value(&row.profile),
                sampled_requests: row.sampled_requests,
                blocked_requests: row.blocked_requests,
                throttled_requests: row.throttled_requests,
                false_positive_hints: row.false_positive_hints,
                avg_response_time_ms: row.avg_response_time_ms.unwrap_or(0.0),
                success_rate: row.success_rate.unwrap_or(1.0),
            },
        );
    }

    let mut metrics = Vec::new();
    for profile in [
        SecurityProfile::Default,
        SecurityProfile::Adaptive,
        SecurityProfile::Strict,
    ] {
        if let Some(existing) = by_profile.remove(profile.as_str()) {
            metrics.push(existing);
        } else {
            metrics.push(SecurityGuardrailProfileMetrics {
                profile,
                sampled_requests: 0,
                blocked_requests: 0,
                throttled_requests: 0,
                false_positive_hints: 0,
                avg_response_time_ms: 0.0,
                success_rate: 1.0,
            });
        }
    }

    Ok(Json(SecurityGuardrailDashboardResponse {
        generated_at: Utc::now(),
        rollout,
        kill_switch_active: kill_switch.is_active,
        profile_metrics: metrics,
    }))
}

pub fn admin_router() -> Router<AppState> {
    Router::new()
        .route(
            "/v1/admin/security/kill-switch",
            get(get_kill_switch_status),
        )
        .route(
            "/v1/admin/security/kill-switch/activate",
            post(activate_kill_switch),
        )
        .route(
            "/v1/admin/security/kill-switch/deactivate",
            post(deactivate_kill_switch),
        )
        .route(
            "/v1/admin/security/kill-switch/audit",
            get(list_kill_switch_audit),
        )
        .route(
            "/v1/admin/security/telemetry/abuse",
            get(list_security_abuse_telemetry),
        )
        .route(
            "/v1/admin/security/profiles/rollout",
            get(get_security_profile_rollout).post(update_security_profile_rollout),
        )
        .route(
            "/v1/admin/security/profiles/overrides/{user_id}",
            post(upsert_security_profile_override).delete(delete_security_profile_override),
        )
        .route(
            "/v1/admin/security/profiles/decisions",
            post(record_rollout_decision).get(list_rollout_decisions),
        )
        .route(
            "/v1/admin/security/guardrails/dashboard",
            get(get_security_guardrail_dashboard),
        )
}

#[cfg(test)]
mod tests {
    use super::{
        UpdateSecurityProfileRolloutRequest, normalize_reason, validate_rollout_config_request,
    };
    use crate::security_profile::SecurityProfile;

    #[test]
    fn normalize_reason_uses_fallback_for_empty_input() {
        assert_eq!(
            normalize_reason(Some("   ".to_string()), "fallback"),
            "fallback"
        );
        assert_eq!(normalize_reason(None, "fallback"), "fallback");
    }

    #[test]
    fn normalize_reason_preserves_trimmed_value() {
        assert_eq!(
            normalize_reason(Some("  incident detected  ".to_string()), "fallback"),
            "incident detected"
        );
    }

    #[test]
    fn rollout_validation_rejects_over_budget_percentages() {
        let req = UpdateSecurityProfileRolloutRequest {
            default_profile: SecurityProfile::Default,
            adaptive_rollout_percent: 60,
            strict_rollout_percent: 50,
            notes: None,
        };
        assert!(validate_rollout_config_request(&req).is_err());
    }
}
