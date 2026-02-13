use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use kura_core::events::{BatchEventWarning, CreateEventRequest, EventMetadata};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::LazyLock;
use std::time::{Duration, Instant};
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::projections::{Projection, ProjectionFreshness, ProjectionMeta, ProjectionResponse};

use crate::auth::{AuthenticatedUser, require_scopes};
use crate::error::AppError;
use crate::routes::events::create_events_batch_internal;
use crate::routes::system::SystemConfigResponse;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/agent/capabilities", get(get_agent_capabilities))
        .route("/v1/agent/context", get(get_agent_context))
        .route(
            "/v1/agent/evidence/event/{event_id}",
            get(get_event_evidence_lineage),
        )
        .route(
            "/v1/agent/visualization/resolve",
            post(resolve_visualization),
        )
        .route("/v1/agent/write-with-proof", post(write_with_proof))
}

#[derive(Deserialize, utoipa::IntoParams)]
pub struct AgentContextParams {
    /// Maximum number of exercise_progression projections to include (default 5, max 100)
    #[serde(default)]
    pub exercise_limit: Option<i64>,
    /// Maximum number of strength_inference projections to include (default 5, max 100)
    #[serde(default)]
    pub strength_limit: Option<i64>,
    /// Maximum number of custom projections to include (default 10, max 100)
    #[serde(default)]
    pub custom_limit: Option<i64>,
    /// Optional task intent string used for context ranking (e.g. "bench plateau")
    #[serde(default)]
    pub task_intent: Option<String>,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct AgentContextSystemContract {
    pub profile: String,
    pub schema_version: String,
    pub default_unknown_field_action: String,
    pub redacted_field_classes: Vec<String>,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct AgentContextMeta {
    pub generated_at: DateTime<Utc>,
    pub exercise_limit: i64,
    pub strength_limit: i64,
    pub custom_limit: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_intent: Option<String>,
    pub ranking_strategy: String,
    pub context_contract_version: String,
    pub system_contract: AgentContextSystemContract,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct AgentContextResponse {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub system: Option<SystemConfigResponse>,
    pub user_profile: ProjectionResponse,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub training_timeline: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_feedback: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub body_composition: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recovery: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub nutrition: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub training_plan: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub semantic_memory: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub readiness_inference: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub causal_inference: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub quality_health: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub exercise_progression: Vec<ProjectionResponse>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub strength_inference: Vec<ProjectionResponse>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub custom: Vec<ProjectionResponse>,
    pub meta: AgentContextMeta,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentVerificationContract {
    pub requires_receipts: bool,
    pub requires_read_after_write: bool,
    pub required_claim_guard_field: String,
    pub saved_claim_condition: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentFallbackContract {
    pub endpoint: String,
    pub compatibility_status: String,
    pub action_hint: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentUpgradePhase {
    pub phase: String,
    pub compatibility_status: String,
    pub starts_at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ends_at: Option<String>,
    pub action_hint: String,
    pub applies_to_endpoints: Vec<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentUpgradePolicy {
    pub current_phase: String,
    pub phases: Vec<AgentUpgradePhase>,
    pub upgrade_signal_header: String,
    pub docs_hint: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentCapabilitiesResponse {
    pub schema_version: String,
    pub protocol_version: String,
    pub preferred_read_endpoint: String,
    pub preferred_write_endpoint: String,
    pub required_verification_contract: AgentVerificationContract,
    pub supported_fallbacks: Vec<AgentFallbackContract>,
    pub min_cli_version: String,
    pub min_mcp_version: String,
    pub upgrade_policy: AgentUpgradePolicy,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentAutonomyPolicy {
    pub policy_version: String,
    pub slo_status: String,
    pub calibration_status: String,
    pub throttle_active: bool,
    pub max_scope_level: String,
    pub require_confirmation_for_non_trivial_actions: bool,
    pub require_confirmation_for_plan_updates: bool,
    pub require_confirmation_for_repairs: bool,
    pub repair_auto_apply_enabled: bool,
    pub reason: String,
    pub confirmation_templates: HashMap<String, String>,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct AgentReadAfterWriteTarget {
    pub projection_type: String,
    pub key: String,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct AgentWriteWithProofRequest {
    pub events: Vec<CreateEventRequest>,
    /// Projection targets that must prove read-after-write before "saved" claims.
    pub read_after_write_targets: Vec<AgentReadAfterWriteTarget>,
    /// Max verification wait (default 1200ms, clamped to 100..10000).
    #[serde(default)]
    pub verify_timeout_ms: Option<u64>,
    /// Include technical repair diagnostics (event IDs, field diffs, command trace).
    /// Default: false (plain-language feedback only).
    #[serde(default)]
    pub include_repair_technical_details: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, utoipa::ToSchema)]
pub struct AgentVisualizationDataSource {
    pub projection_type: String,
    pub key: String,
    /// Dot-path inside projection.data (e.g. weekly_summary.0.total_volume_kg)
    #[serde(default)]
    pub json_path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, utoipa::ToSchema)]
pub struct AgentVisualizationSpec {
    /// chart | table | timeline | ascii | mermaid
    pub format: String,
    /// Human-purpose of the visualization (e.g. "4-week volume trend")
    pub purpose: String,
    #[serde(default)]
    pub title: Option<String>,
    /// Optional explicit IANA timezone for date/week semantics
    #[serde(default)]
    pub timezone: Option<String>,
    pub data_sources: Vec<AgentVisualizationDataSource>,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct AgentResolveVisualizationRequest {
    pub task_intent: String,
    /// auto | always | never
    #[serde(default)]
    pub user_preference_override: Option<String>,
    /// low | medium | high
    #[serde(default)]
    pub complexity_hint: Option<String>,
    /// If false, rich formats are converted to ASCII fallback.
    #[serde(default = "default_true")]
    pub allow_rich_rendering: bool,
    /// Required only when policy decides visualization is useful.
    #[serde(default)]
    pub visualization_spec: Option<AgentVisualizationSpec>,
    /// Optional session identifier used for telemetry metadata.
    #[serde(default)]
    pub telemetry_session_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentVisualizationPolicyDecision {
    /// visualize | skipped | fallback
    pub status: String,
    /// trend | compare | plan_vs_actual | multi_week_scheduling | user_preference_always | user_preference_never | none
    pub trigger: String,
    /// auto | always | never
    pub preference_mode: String,
    /// low | medium | high
    pub complexity: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentVisualizationResolvedSource {
    pub projection_type: String,
    pub key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub json_path: Option<String>,
    pub projection_version: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub projection_last_event_id: Option<Uuid>,
    pub value: Value,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentVisualizationTimezoneContext {
    pub timezone: String,
    pub assumed: bool,
    /// spec | user_profile.preference | fallback_utc
    pub source: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentVisualizationOutput {
    /// chart | table | timeline | ascii | mermaid | text
    pub format: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentResolveVisualizationResponse {
    pub policy: AgentVisualizationPolicyDecision,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub visualization_spec: Option<AgentVisualizationSpec>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub resolved_sources: Vec<AgentVisualizationResolvedSource>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timezone_context: Option<AgentVisualizationTimezoneContext>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uncertainty_label: Option<String>,
    pub output: AgentVisualizationOutput,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fallback_output: Option<AgentVisualizationOutput>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub telemetry_signal_types: Vec<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentWorkflowGate {
    /// onboarding | planning
    pub phase: String,
    /// allowed | blocked
    pub status: String,
    /// none | onboarding_closed | override
    pub transition: String,
    pub onboarding_closed: bool,
    pub override_used: bool,
    pub message: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub missing_requirements: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub planning_event_types: Vec<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentWriteReceipt {
    pub event_id: Uuid,
    pub event_type: String,
    pub idempotency_key: String,
    pub event_timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentReadAfterWriteCheck {
    pub projection_type: String,
    pub key: String,
    /// verified | pending
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub observed_projection_version: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub observed_last_event_id: Option<Uuid>,
    pub detail: String,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentWriteVerificationSummary {
    /// verified | pending | failed
    pub status: String,
    pub checked_at: DateTime<Utc>,
    pub waited_ms: u64,
    /// fresh_write | idempotent_retry
    pub write_path: String,
    pub required_checks: usize,
    pub verified_checks: usize,
    pub checks: Vec<AgentReadAfterWriteCheck>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentWriteClaimGuard {
    pub allow_saved_claim: bool,
    /// saved_verified | pending | failed
    pub claim_status: String,
    pub uncertainty_markers: Vec<String>,
    pub deferred_markers: Vec<String>,
    pub recommended_user_phrase: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_action_confirmation_prompt: Option<String>,
    pub autonomy_policy: AgentAutonomyPolicy,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentSessionAuditSummary {
    /// clean | repaired | needs_clarification
    pub status: String,
    pub mismatch_detected: usize,
    pub mismatch_repaired: usize,
    pub mismatch_unresolved: usize,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub mismatch_classes: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub clarification_question: Option<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentRepairReceipt {
    /// none | repaired | needs_clarification
    pub status: String,
    pub changed_fields_count: usize,
    pub unchanged_metrics: HashMap<String, Value>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentUndoEventTemplate {
    pub timestamp: DateTime<Utc>,
    pub event_type: String,
    pub data: Value,
    pub metadata: EventMetadata,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentRepairUndoAction {
    pub available: bool,
    pub detail: String,
    pub events: Vec<AgentUndoEventTemplate>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentRepairFieldDiff {
    pub target_event_id: String,
    pub field: String,
    pub value: Value,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentRepairTechnicalDetails {
    pub repair_event_ids: Vec<Uuid>,
    pub target_event_ids: Vec<String>,
    pub field_diffs: Vec<AgentRepairFieldDiff>,
    pub command_trace: Vec<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentRepairFeedback {
    /// none | repaired | needs_clarification
    pub status: String,
    pub summary: String,
    pub receipt: AgentRepairReceipt,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub clarification_question: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub undo: Option<AgentRepairUndoAction>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub technical: Option<AgentRepairTechnicalDetails>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentReliabilityInferredFact {
    pub field: String,
    pub confidence: f64,
    pub provenance: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentReliabilityUx {
    /// saved | inferred | unresolved
    pub state: String,
    pub assistant_phrase: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub inferred_facts: Vec<AgentReliabilityInferredFact>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub clarification_question: Option<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentWriteWithProofResponse {
    pub receipts: Vec<AgentWriteReceipt>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<BatchEventWarning>,
    pub verification: AgentWriteVerificationSummary,
    pub claim_guard: AgentWriteClaimGuard,
    pub reliability_ux: AgentReliabilityUx,
    pub workflow_gate: AgentWorkflowGate,
    pub session_audit: AgentSessionAuditSummary,
    pub repair_feedback: AgentRepairFeedback,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentEvidenceClaim {
    pub claim_event_id: Uuid,
    pub claim_id: String,
    pub claim_type: String,
    pub value: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub unit: Option<String>,
    pub scope: Value,
    pub confidence: f64,
    pub provenance: Value,
    pub lineage: Value,
    pub recorded_at: DateTime<Utc>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentEventEvidenceResponse {
    pub event_id: Uuid,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub claims: Vec<AgentEvidenceClaim>,
}

#[derive(sqlx::FromRow)]
struct ProjectionRow {
    id: Uuid,
    user_id: Uuid,
    projection_type: String,
    key: String,
    data: Value,
    version: i64,
    last_event_id: Option<Uuid>,
    updated_at: DateTime<Utc>,
}

impl ProjectionRow {
    fn into_response(self, now: DateTime<Utc>) -> ProjectionResponse {
        let computed_at = self.updated_at;
        let meta = ProjectionMeta {
            projection_version: self.version,
            computed_at,
            freshness: ProjectionFreshness::from_computed_at(computed_at, now),
        };
        ProjectionResponse {
            projection: Projection {
                id: self.id,
                user_id: self.user_id,
                projection_type: self.projection_type,
                key: self.key,
                data: self.data,
                version: self.version,
                last_event_id: self.last_event_id,
                updated_at: computed_at,
            },
            meta,
        }
    }
}

#[derive(sqlx::FromRow)]
struct SystemConfigRow {
    data: Value,
    version: i64,
    updated_at: DateTime<Utc>,
}

#[derive(sqlx::FromRow)]
struct EvidenceClaimEventRow {
    id: Uuid,
    timestamp: DateTime<Utc>,
    data: Value,
}

#[derive(sqlx::FromRow)]
struct ExistingWriteReceiptRow {
    id: Uuid,
    event_type: String,
    timestamp: DateTime<Utc>,
    metadata: Value,
}

#[derive(sqlx::FromRow)]
struct WorkflowMarkerEventRow {
    id: Uuid,
    event_type: String,
}

#[derive(sqlx::FromRow)]
struct RetractedMarkerRow {
    retracted_event_id: Option<String>,
}

#[derive(Debug)]
struct SessionAuditArtifacts {
    summary: AgentSessionAuditSummary,
    repair_events: Vec<CreateEventRequest>,
    telemetry_events: Vec<CreateEventRequest>,
}

#[derive(Debug)]
struct SessionAuditUnresolved {
    exercise_label: String,
    field: String,
    candidates: Vec<String>,
}

#[derive(Debug, Clone)]
struct AgentWorkflowState {
    onboarding_closed: bool,
    override_active: bool,
    missing_close_requirements: Vec<String>,
    legacy_planning_history: bool,
}

fn recover_receipts_for_idempotent_retry(
    requested_events: &[CreateEventRequest],
    recovered_by_key: &HashMap<String, AgentWriteReceipt>,
) -> Vec<AgentWriteReceipt> {
    let mut receipts = Vec::with_capacity(requested_events.len());
    for event in requested_events {
        let key = event.metadata.idempotency_key.trim();
        if key.is_empty() {
            continue;
        }
        if let Some(receipt) = recovered_by_key.get(key) {
            receipts.push(receipt.clone());
        }
    }
    receipts
}

async fn fetch_existing_receipts_by_idempotency_keys(
    state: &AppState,
    user_id: Uuid,
    keys: &[String],
) -> Result<HashMap<String, AgentWriteReceipt>, AppError> {
    if keys.is_empty() {
        return Ok(HashMap::new());
    }

    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let rows = sqlx::query_as::<_, ExistingWriteReceiptRow>(
        r#"
        SELECT id, event_type, timestamp, metadata
        FROM events
        WHERE user_id = $1
          AND metadata->>'idempotency_key' = ANY($2)
        ORDER BY timestamp ASC, id ASC
        "#,
    )
    .bind(user_id)
    .bind(keys)
    .fetch_all(&mut *tx)
    .await?;

    tx.commit().await?;

    let mut recovered = HashMap::new();
    for row in rows {
        let key = row
            .metadata
            .get("idempotency_key")
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or_default()
            .to_string();
        if key.is_empty() || recovered.contains_key(&key) {
            continue;
        }
        recovered.insert(
            key.clone(),
            AgentWriteReceipt {
                event_id: row.id,
                event_type: row.event_type,
                idempotency_key: key,
                event_timestamp: row.timestamp,
            },
        );
    }

    Ok(recovered)
}

fn to_write_receipts(events: &[kura_core::events::Event]) -> Vec<AgentWriteReceipt> {
    events
        .iter()
        .map(|event| AgentWriteReceipt {
            event_id: event.id,
            event_type: event.event_type.clone(),
            idempotency_key: event.metadata.idempotency_key.clone(),
            event_timestamp: event.timestamp,
        })
        .collect()
}

async fn write_events_with_receipts(
    state: &AppState,
    user_id: Uuid,
    events: &[CreateEventRequest],
    warning_field: &str,
) -> Result<(Vec<AgentWriteReceipt>, Vec<BatchEventWarning>, String), AppError> {
    if events.is_empty() {
        return Ok((Vec::new(), Vec::new(), "fresh_write".to_string()));
    }

    let mut warnings: Vec<BatchEventWarning> = Vec::new();
    let mut write_path = "fresh_write".to_string();
    let receipts: Vec<AgentWriteReceipt> = match create_events_batch_internal(
        state, user_id, events,
    )
    .await
    {
        Ok(batch_result) => {
            warnings = batch_result.warnings;
            to_write_receipts(&batch_result.events)
        }
        Err(AppError::IdempotencyConflict { .. }) => {
            write_path = "idempotent_retry".to_string();
            let requested_keys: Vec<String> = events
                .iter()
                .map(|event| event.metadata.idempotency_key.clone())
                .collect();
            let recovered_by_key =
                fetch_existing_receipts_by_idempotency_keys(state, user_id, &requested_keys)
                    .await?;
            let recovered = recover_receipts_for_idempotent_retry(events, &recovered_by_key);
            let recovered_count = recovered.len();
            let requested_count = events.len();
            let recovery_message = if recovered_count == requested_count {
                "Idempotent retry detected; reused existing write receipts.".to_string()
            } else {
                format!(
                    "Idempotent retry detected but recovery is incomplete ({recovered_count}/{requested_count} receipts)."
                )
            };
            warnings.push(BatchEventWarning {
                event_index: 0,
                field: warning_field.to_string(),
                message: recovery_message,
                severity: "warning".to_string(),
            });
            recovered
        }
        Err(err) => return Err(err),
    };

    Ok((receipts, warnings, write_path))
}

fn build_agent_capabilities() -> AgentCapabilitiesResponse {
    AgentCapabilitiesResponse {
        schema_version: "agent_capabilities.v1".to_string(),
        protocol_version: "2026-02-11.agent-contract.v1".to_string(),
        preferred_read_endpoint: "/v1/agent/context".to_string(),
        preferred_write_endpoint: "/v1/agent/write-with-proof".to_string(),
        required_verification_contract: AgentVerificationContract {
            requires_receipts: true,
            requires_read_after_write: true,
            required_claim_guard_field: "claim_guard.allow_saved_claim".to_string(),
            saved_claim_condition: "allow_saved_claim=true".to_string(),
        },
        supported_fallbacks: vec![
            AgentFallbackContract {
                endpoint: "/v1/events".to_string(),
                compatibility_status: "supported_with_upgrade_signal".to_string(),
                action_hint: "Prefer /v1/agent/write-with-proof for agent writes.".to_string(),
                reason: "Legacy event writes do not enforce read-after-write proof.".to_string(),
            },
            AgentFallbackContract {
                endpoint: "/v1/events/batch".to_string(),
                compatibility_status: "supported_with_upgrade_signal".to_string(),
                action_hint: "Prefer /v1/agent/write-with-proof for agent writes.".to_string(),
                reason: "Legacy batch writes do not return claim guard verification.".to_string(),
            },
            AgentFallbackContract {
                endpoint: "/v1/projections".to_string(),
                compatibility_status: "supported_with_upgrade_signal".to_string(),
                action_hint: "Prefer /v1/agent/context for bundled agent reads.".to_string(),
                reason: "Snapshot reads miss contract-level ranking and bundle guarantees."
                    .to_string(),
            },
            AgentFallbackContract {
                endpoint: "/v1/projections/{projection_type}/{key}".to_string(),
                compatibility_status: "supported_with_upgrade_signal".to_string(),
                action_hint: "Prefer /v1/agent/context for bundled agent reads.".to_string(),
                reason: "Direct projection reads bypass context bundle semantics.".to_string(),
            },
        ],
        min_cli_version: env!("CARGO_PKG_VERSION").to_string(),
        min_mcp_version: "not_implemented".to_string(),
        upgrade_policy: AgentUpgradePolicy {
            current_phase: "supported_with_upgrade_signals".to_string(),
            phases: vec![
                AgentUpgradePhase {
                    phase: "supported".to_string(),
                    compatibility_status: "supported".to_string(),
                    starts_at: "2026-02-11".to_string(),
                    ends_at: Some("2026-04-30".to_string()),
                    action_hint: "Clients may keep legacy flows during migration.".to_string(),
                    applies_to_endpoints: vec![
                        "/v1/events".to_string(),
                        "/v1/events/batch".to_string(),
                        "/v1/projections".to_string(),
                        "/v1/projections/{projection_type}/{key}".to_string(),
                    ],
                },
                AgentUpgradePhase {
                    phase: "deprecated".to_string(),
                    compatibility_status: "deprecated".to_string(),
                    starts_at: "2026-05-01".to_string(),
                    ends_at: Some("2026-08-31".to_string()),
                    action_hint: "Migrate to /v1/agent/context and /v1/agent/write-with-proof."
                        .to_string(),
                    applies_to_endpoints: vec![
                        "/v1/events".to_string(),
                        "/v1/events/batch".to_string(),
                        "/v1/projections".to_string(),
                        "/v1/projections/{projection_type}/{key}".to_string(),
                    ],
                },
                AgentUpgradePhase {
                    phase: "removed".to_string(),
                    compatibility_status: "planned".to_string(),
                    starts_at: "2026-09-01".to_string(),
                    ends_at: None,
                    action_hint:
                        "Legacy agent flows must be routed through agent contract endpoints."
                            .to_string(),
                    applies_to_endpoints: vec![
                        "/v1/events".to_string(),
                        "/v1/events/batch".to_string(),
                        "/v1/projections".to_string(),
                        "/v1/projections/{projection_type}/{key}".to_string(),
                    ],
                },
            ],
            upgrade_signal_header: "x-kura-upgrade-signal".to_string(),
            docs_hint: "Discover preferred contracts via /v1/agent/capabilities.".to_string(),
        },
    }
}

const AGENT_CONTEXT_CONTRACT_VERSION: &str = "agent_context.v2.redacted";
const AGENT_CONTEXT_SYSTEM_CONTRACT_VERSION: &str = "agent_context.system.v1";
const AGENT_CONTEXT_SYSTEM_PROFILE: &str = "redacted_v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SystemConfigFieldClass {
    PublicContract,
    SensitiveGuidance,
    InternalStrategy,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SystemConventionFieldClass {
    PublicContract,
    InternalOperations,
    Unknown,
}

fn classify_system_config_field(key: &str) -> SystemConfigFieldClass {
    match key {
        "dimensions" | "event_conventions" | "projection_schemas" | "conventions" => {
            SystemConfigFieldClass::PublicContract
        }
        "interview_guide" => SystemConfigFieldClass::SensitiveGuidance,
        "agent_behavior" => SystemConfigFieldClass::InternalStrategy,
        _ => SystemConfigFieldClass::Unknown,
    }
}

fn classify_system_convention_field(key: &str) -> SystemConventionFieldClass {
    match key {
        "exercise_normalization"
        | "training_core_fields_v1"
        | "evidence_layer_v1"
        | "open_observation_v1"
        | "ingestion_locale_v1"
        | "load_context_v1"
        | "session_feedback_certainty_v1"
        | "schema_capability_gate_v1" => SystemConventionFieldClass::PublicContract,
        "learning_clustering_v1"
        | "extraction_calibration_v1"
        | "learning_backlog_bridge_v1"
        | "unknown_dimension_mining_v1"
        | "shadow_evaluation_gate_v1" => SystemConventionFieldClass::InternalOperations,
        _ => SystemConventionFieldClass::Unknown,
    }
}

fn redact_system_conventions_for_agent(value: Value) -> Value {
    let Value::Object(conventions) = value else {
        return Value::Object(serde_json::Map::new());
    };

    let mut redacted = serde_json::Map::new();
    for (key, value) in conventions {
        if matches!(
            classify_system_convention_field(&key),
            SystemConventionFieldClass::PublicContract
        ) {
            redacted.insert(key, value);
        }
    }

    Value::Object(redacted)
}

fn redact_system_config_data_for_agent(value: Value) -> Value {
    let Value::Object(config) = value else {
        return Value::Object(serde_json::Map::new());
    };

    let mut redacted = serde_json::Map::new();
    for (key, value) in config {
        match classify_system_config_field(&key) {
            SystemConfigFieldClass::PublicContract => {
                if key == "conventions" {
                    redacted.insert(key, redact_system_conventions_for_agent(value));
                } else {
                    redacted.insert(key, value);
                }
            }
            SystemConfigFieldClass::SensitiveGuidance
            | SystemConfigFieldClass::InternalStrategy
            | SystemConfigFieldClass::Unknown => {}
        }
    }

    Value::Object(redacted)
}

fn redact_system_config_for_agent(system: SystemConfigResponse) -> SystemConfigResponse {
    SystemConfigResponse {
        data: redact_system_config_data_for_agent(system.data),
        version: system.version,
        updated_at: system.updated_at,
    }
}

fn build_agent_context_system_contract() -> AgentContextSystemContract {
    AgentContextSystemContract {
        profile: AGENT_CONTEXT_SYSTEM_PROFILE.to_string(),
        schema_version: AGENT_CONTEXT_SYSTEM_CONTRACT_VERSION.to_string(),
        default_unknown_field_action: "deny".to_string(),
        redacted_field_classes: vec![
            "system.internal_strategy".to_string(),
            "system.sensitive_guidance".to_string(),
            "system.unknown".to_string(),
            "system.conventions.internal_operations".to_string(),
            "system.conventions.unknown".to_string(),
        ],
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum IntentClass {
    Strength,
    Recovery,
    Nutrition,
    Planning,
    BodyComposition,
    Semantic,
    General,
}

struct RankingContext {
    intent: Option<String>,
    intent_tokens: HashSet<String>,
    intent_class: IntentClass,
    semantic_terms_by_key: HashMap<String, HashSet<String>>,
}

impl RankingContext {
    fn from_task_intent(
        intent: Option<String>,
        semantic_memory: Option<&ProjectionResponse>,
    ) -> Self {
        let normalized_intent = intent.and_then(|raw| {
            let trimmed = raw.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
            }
        });
        let intent_tokens = normalized_intent
            .as_deref()
            .map(tokenize)
            .unwrap_or_default();
        let semantic_terms_by_key = semantic_terms_by_key(semantic_memory);
        let intent_class = classify_intent(&intent_tokens);

        Self {
            intent: normalized_intent,
            intent_tokens,
            intent_class,
            semantic_terms_by_key,
        }
    }
}

struct ScoredProjection {
    response: ProjectionResponse,
    score: f64,
    recency_score: f64,
}

fn tokenize(value: &str) -> HashSet<String> {
    value
        .split(|c: char| !c.is_alphanumeric())
        .filter_map(|chunk| {
            let normalized = chunk.trim().to_lowercase();
            if normalized.is_empty() {
                None
            } else {
                Some(normalized)
            }
        })
        .collect()
}

fn classify_intent(tokens: &HashSet<String>) -> IntentClass {
    let has = |candidates: &[&str]| candidates.iter().any(|t| tokens.contains(*t));

    if has(&[
        "strength",
        "kraft",
        "bench",
        "squat",
        "deadlift",
        "1rm",
        "plateau",
        "hypertrophy",
        "progression",
    ]) {
        return IntentClass::Strength;
    }
    if has(&[
        "readiness",
        "recovery",
        "fatigue",
        "regeneration",
        "ermuedung",
        "sleep",
        "soreness",
    ]) {
        return IntentClass::Recovery;
    }
    if has(&[
        "nutrition",
        "meal",
        "kalorien",
        "kcal",
        "protein",
        "carbs",
        "fat",
        "makro",
    ]) {
        return IntentClass::Nutrition;
    }
    if has(&[
        "plan", "planning", "schedule", "session", "week", "zyklus", "meso", "deload",
    ]) {
        return IntentClass::Planning;
    }
    if has(&[
        "body",
        "bodyweight",
        "weight",
        "waist",
        "fett",
        "bodyfat",
        "composition",
    ]) {
        return IntentClass::BodyComposition;
    }
    if has(&[
        "semantic",
        "alias",
        "resolve",
        "mapping",
        "term",
        "vocabulary",
    ]) {
        return IntentClass::Semantic;
    }
    IntentClass::General
}

fn semantic_terms_by_key(
    semantic_memory: Option<&ProjectionResponse>,
) -> HashMap<String, HashSet<String>> {
    let mut out: HashMap<String, HashSet<String>> = HashMap::new();
    let Some(memory) = semantic_memory else {
        return out;
    };

    let add_terms = |out: &mut HashMap<String, HashSet<String>>,
                     candidates: Option<&Vec<Value>>,
                     key_field: &str| {
        let Some(items) = candidates else {
            return;
        };
        for item in items {
            let Some(candidate) = item.as_object() else {
                continue;
            };
            let Some(key_raw) = candidate.get(key_field).and_then(Value::as_str) else {
                continue;
            };
            let Some(term_raw) = candidate.get("term").and_then(Value::as_str) else {
                continue;
            };
            let key = key_raw.trim().to_lowercase();
            let term = term_raw.trim().to_lowercase();
            if key.is_empty() || term.is_empty() {
                continue;
            }
            out.entry(key).or_default().insert(term);
        }
    };

    add_terms(
        &mut out,
        memory
            .projection
            .data
            .get("exercise_candidates")
            .and_then(Value::as_array),
        "suggested_exercise_id",
    );
    add_terms(
        &mut out,
        memory
            .projection
            .data
            .get("food_candidates")
            .and_then(Value::as_array),
        "suggested_food_id",
    );

    out
}

fn overlap_ratio(a: &HashSet<String>, b: &HashSet<String>) -> f64 {
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let intersection = a.intersection(b).count() as f64;
    (intersection / (b.len() as f64)).clamp(0.0, 1.0)
}

fn json_f64(data: &Value, path: &[&str]) -> Option<f64> {
    let mut cursor = data;
    for key in path {
        cursor = cursor.get(*key)?;
    }
    cursor.as_f64()
}

fn json_bool(data: &Value, path: &[&str]) -> Option<bool> {
    let mut cursor = data;
    for key in path {
        cursor = cursor.get(*key)?;
    }
    cursor.as_bool()
}

fn confidence_score(projection_type: &str, data: &Value) -> f64 {
    if json_bool(data, &["data_quality", "insufficient_data"]).unwrap_or(false) {
        return 0.1;
    }

    match projection_type {
        "strength_inference" => {
            let dynamics =
                json_f64(data, &["dynamics", "estimated_1rm", "confidence"]).unwrap_or(0.0);
            let sessions = json_f64(data, &["data_quality", "sessions_used"]).unwrap_or(0.0);
            let sessions_score = (sessions / 8.0).clamp(0.0, 1.0);
            let ci_score = data
                .get("trend")
                .and_then(|t| t.get("slope_ci95"))
                .and_then(Value::as_array)
                .and_then(|ci| {
                    if ci.len() != 2 {
                        return None;
                    }
                    let low = ci[0].as_f64()?;
                    let high = ci[1].as_f64()?;
                    let width = (high - low).abs();
                    Some((1.0 / (1.0 + (width * 20.0))).clamp(0.2, 1.0))
                })
                .unwrap_or(0.6);

            (0.5 * dynamics + 0.3 * sessions_score + 0.2 * ci_score).clamp(0.05, 1.0)
        }
        "exercise_progression" => {
            let total_sets = data
                .get("total_sets")
                .and_then(Value::as_f64)
                .unwrap_or(0.0);
            let total_sessions = data
                .get("total_sessions")
                .and_then(Value::as_f64)
                .unwrap_or(0.0);
            let anomaly_count = data
                .get("data_quality")
                .and_then(|dq| dq.get("anomalies"))
                .and_then(Value::as_array)
                .map(|items| items.len())
                .unwrap_or(0);

            let volume_score =
                (0.6 * (total_sets / 30.0) + 0.4 * (total_sessions / 12.0)).clamp(0.0, 1.0);
            let anomaly_penalty = ((anomaly_count as f64) * 0.08).min(0.5);
            (volume_score * (1.0 - anomaly_penalty)).clamp(0.1, 1.0)
        }
        "custom" => {
            let total_events =
                json_f64(data, &["data_quality", "total_events_processed"]).unwrap_or(0.0);
            (total_events / 40.0).clamp(0.1, 1.0)
        }
        "session_feedback" => {
            let sessions = json_f64(data, &["counts", "sessions_with_feedback"]).unwrap_or(0.0);
            (sessions / 12.0).clamp(0.1, 1.0)
        }
        _ => 0.5,
    }
}

fn recency_score(projection_type: &str, updated_at: DateTime<Utc>, now: DateTime<Utc>) -> f64 {
    let age_hours = now.signed_duration_since(updated_at).num_seconds().max(0) as f64 / 3600.0;
    let half_life_hours = match projection_type {
        "strength_inference" => 72.0,
        "exercise_progression" => 96.0,
        "custom" => 168.0,
        _ => 120.0,
    };
    2.0_f64.powf(-age_hours / half_life_hours)
}

fn intent_alignment_score(projection_type: &str, intent: IntentClass) -> f64 {
    match intent {
        IntentClass::Strength => match projection_type {
            "strength_inference" => 1.0,
            "exercise_progression" => 0.95,
            "training_timeline" => 0.7,
            "training_plan" => 0.65,
            "custom" => 0.6,
            "readiness_inference" => 0.55,
            _ => 0.45,
        },
        IntentClass::Recovery => match projection_type {
            "readiness_inference" => 1.0,
            "recovery" => 0.95,
            "session_feedback" => 0.75,
            "training_timeline" => 0.75,
            "strength_inference" => 0.55,
            "custom" => 0.6,
            _ => 0.45,
        },
        IntentClass::Nutrition => match projection_type {
            "nutrition" => 1.0,
            "body_composition" => 0.75,
            "custom" => 0.7,
            "training_timeline" => 0.5,
            _ => 0.4,
        },
        IntentClass::Planning => match projection_type {
            "training_plan" => 1.0,
            "training_timeline" => 0.9,
            "session_feedback" => 0.85,
            "readiness_inference" => 0.65,
            "strength_inference" => 0.65,
            "exercise_progression" => 0.6,
            "custom" => 0.65,
            _ => 0.45,
        },
        IntentClass::BodyComposition => match projection_type {
            "body_composition" => 1.0,
            "nutrition" => 0.75,
            "custom" => 0.65,
            _ => 0.45,
        },
        IntentClass::Semantic => match projection_type {
            "semantic_memory" => 1.0,
            "exercise_progression" => 0.6,
            "strength_inference" => 0.55,
            "custom" => 0.5,
            _ => 0.45,
        },
        IntentClass::General => match projection_type {
            "strength_inference" => 0.8,
            "exercise_progression" => 0.8,
            "session_feedback" => 0.7,
            "custom" => 0.65,
            _ => 0.6,
        },
    }
}

fn semantic_relevance_score(
    projection_type: &str,
    key: &str,
    data: &Value,
    context: &RankingContext,
) -> f64 {
    if context.intent_tokens.is_empty() {
        return 0.5;
    }

    let mut best = overlap_ratio(&tokenize(&key.replace('_', " ")), &context.intent_tokens);

    if projection_type == "strength_inference" || projection_type == "exercise_progression" {
        if let Some(exercise_id) = data.get("exercise_id").and_then(Value::as_str) {
            best = best.max(overlap_ratio(
                &tokenize(&exercise_id.replace('_', " ")),
                &context.intent_tokens,
            ));
        }
        if let Some(terms) = context.semantic_terms_by_key.get(&key.to_lowercase()) {
            for term in terms {
                best = best.max(overlap_ratio(&tokenize(term), &context.intent_tokens));
            }
        }
    }

    best.clamp(0.0, 1.0)
}

fn projection_score(
    response: ProjectionResponse,
    context: &RankingContext,
    now: DateTime<Utc>,
) -> ScoredProjection {
    let projection_type = response.projection.projection_type.as_str();
    let key = response.projection.key.as_str();
    let recency = recency_score(projection_type, response.projection.updated_at, now);
    let confidence = confidence_score(projection_type, &response.projection.data);
    let semantic =
        semantic_relevance_score(projection_type, key, &response.projection.data, context);
    let intent = intent_alignment_score(projection_type, context.intent_class);

    let (w_recency, w_confidence, w_semantic, w_intent) = if context.intent_tokens.is_empty() {
        (0.6, 0.4, 0.0, 0.0)
    } else {
        (0.35, 0.25, 0.2, 0.2)
    };

    let score = (w_recency * recency)
        + (w_confidence * confidence)
        + (w_semantic * semantic)
        + (w_intent * intent);

    ScoredProjection {
        response,
        score,
        recency_score: recency,
    }
}

fn ranking_candidate_limit(limit: i64) -> i64 {
    (limit.saturating_mul(5)).clamp(limit, 500)
}

fn rank_projection_list(
    candidates: Vec<ProjectionResponse>,
    limit: i64,
    context: &RankingContext,
) -> Vec<ProjectionResponse> {
    let now = Utc::now();
    let mut scored: Vec<ScoredProjection> = candidates
        .into_iter()
        .map(|candidate| projection_score(candidate, context, now))
        .collect();

    scored.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| {
                b.recency_score
                    .partial_cmp(&a.recency_score)
                    .unwrap_or(Ordering::Equal)
            })
            .then_with(|| a.response.projection.key.cmp(&b.response.projection.key))
    });

    scored
        .into_iter()
        .take(limit.max(0) as usize)
        .map(|item| item.response)
        .collect()
}

fn clamp_limit(value: Option<i64>, default: i64, max: i64) -> i64 {
    value.unwrap_or(default).max(1).min(max)
}

fn bootstrap_user_profile(user_id: Uuid) -> ProjectionResponse {
    let now = Utc::now();
    ProjectionResponse {
        projection: Projection {
            id: Uuid::nil(),
            user_id,
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            data: serde_json::json!({
                "user": null,
                "agenda": [{
                    "priority": "high",
                    "type": "onboarding_needed",
                    "detail": "New user. No data yet. Produce initial events to bootstrap profile.",
                    "dimensions": ["user_profile"]
                }]
            }),
            version: 0,
            last_event_id: None,
            updated_at: now,
        },
        meta: ProjectionMeta {
            projection_version: 0,
            computed_at: now,
            freshness: ProjectionFreshness::from_computed_at(now, now),
        },
    }
}

async fn fetch_projection(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
    projection_type: &str,
    key: &str,
) -> Result<Option<ProjectionResponse>, AppError> {
    let now = Utc::now();
    let row = sqlx::query_as::<_, ProjectionRow>(
        r#"
        SELECT id, user_id, projection_type, key, data, version, last_event_id, updated_at
        FROM projections
        WHERE user_id = $1 AND projection_type = $2 AND key = $3
        "#,
    )
    .bind(user_id)
    .bind(projection_type)
    .bind(key)
    .fetch_optional(&mut **tx)
    .await?;

    Ok(row.map(|r| r.into_response(now)))
}

async fn fetch_quality_health_projection(
    state: &AppState,
    user_id: Uuid,
) -> Result<Option<ProjectionResponse>, AppError> {
    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;
    let projection = fetch_projection(&mut tx, user_id, "quality_health", "overview").await?;
    tx.commit().await?;
    Ok(projection)
}

async fn fetch_user_profile_projection(
    state: &AppState,
    user_id: Uuid,
) -> Result<Option<ProjectionResponse>, AppError> {
    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;
    let projection = fetch_projection(&mut tx, user_id, "user_profile", "me").await?;
    tx.commit().await?;
    Ok(projection)
}

async fn resolve_visualization_sources(
    state: &AppState,
    user_id: Uuid,
    spec: &AgentVisualizationSpec,
) -> Result<Vec<AgentVisualizationResolvedSource>, AppError> {
    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let mut resolved_sources = Vec::with_capacity(spec.data_sources.len());
    let mut unresolved_references: Vec<String> = Vec::new();

    for source in &spec.data_sources {
        let projection =
            fetch_projection(&mut tx, user_id, &source.projection_type, &source.key).await?;
        let Some(projection) = projection else {
            unresolved_references.push(format!("{}:{}", source.projection_type, source.key));
            continue;
        };
        match bind_visualization_source(source, &projection) {
            Ok(bound) => resolved_sources.push(bound),
            Err(detail) => unresolved_references.push(detail),
        }
    }

    tx.commit().await?;

    if unresolved_references.is_empty() {
        return Ok(resolved_sources);
    }

    Err(AppError::Validation {
        message: "visualization_spec contains unresolved projection references".to_string(),
        field: Some("visualization_spec.data_sources".to_string()),
        received: Some(serde_json::json!({ "unresolved": unresolved_references })),
        docs_hint: Some(
            "Ensure each projection_type/key exists and json_path points to an existing data field."
                .to_string(),
        ),
    })
}

async fn fetch_workflow_state(
    state: &AppState,
    user_id: Uuid,
    user_profile: Option<&ProjectionResponse>,
) -> Result<AgentWorkflowState, AppError> {
    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let marker_rows = sqlx::query_as::<_, WorkflowMarkerEventRow>(
        r#"
        SELECT id, event_type
        FROM events
        WHERE user_id = $1
          AND event_type IN ($2, $3)
        ORDER BY timestamp ASC, id ASC
        "#,
    )
    .bind(user_id)
    .bind(WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE)
    .bind(WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE)
    .fetch_all(&mut *tx)
    .await?;

    let planning_event_types: Vec<&str> = PLANNING_OR_COACHING_EVENT_TYPES.to_vec();
    let legacy_planning_history = sqlx::query_scalar::<_, bool>(
        r#"
        SELECT EXISTS(
            SELECT 1
            FROM events e
            WHERE e.user_id = $1
              AND e.event_type = ANY($2)
              AND NOT EXISTS (
                SELECT 1
                FROM events retracted
                WHERE retracted.user_id = $1
                  AND retracted.event_type = 'event.retracted'
                  AND retracted.data->>'retracted_event_id' = e.id::text
              )
        )
        "#,
    )
    .bind(user_id)
    .bind(&planning_event_types)
    .fetch_one(&mut *tx)
    .await?;

    let marker_ids: Vec<String> = marker_rows.iter().map(|row| row.id.to_string()).collect();
    let retracted_ids: HashSet<String> = if marker_ids.is_empty() {
        HashSet::new()
    } else {
        sqlx::query_as::<_, RetractedMarkerRow>(
            r#"
            SELECT data->>'retracted_event_id' AS retracted_event_id
            FROM events
            WHERE user_id = $1
              AND event_type = 'event.retracted'
              AND data->>'retracted_event_id' = ANY($2)
            "#,
        )
        .bind(user_id)
        .bind(&marker_ids)
        .fetch_all(&mut *tx)
        .await?
        .into_iter()
        .filter_map(|row| row.retracted_event_id)
        .collect()
    };

    tx.commit().await?;

    let active_markers: Vec<&WorkflowMarkerEventRow> = marker_rows
        .iter()
        .filter(|row| !retracted_ids.contains(&row.id.to_string()))
        .collect();
    let onboarding_closed = active_markers.iter().any(|row| {
        row.event_type
            .eq_ignore_ascii_case(WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE)
    });
    let override_active = active_markers.iter().any(|row| {
        row.event_type
            .eq_ignore_ascii_case(WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE)
    });

    Ok(AgentWorkflowState {
        onboarding_closed,
        override_active,
        legacy_planning_history,
        missing_close_requirements: if onboarding_closed {
            Vec::new()
        } else {
            missing_onboarding_close_requirements(user_profile)
        },
    })
}

async fn fetch_projection_list(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
    projection_type: &str,
    limit: i64,
) -> Result<Vec<ProjectionResponse>, AppError> {
    let now = Utc::now();
    let rows = sqlx::query_as::<_, ProjectionRow>(
        r#"
        SELECT id, user_id, projection_type, key, data, version, last_event_id, updated_at
        FROM projections
        WHERE user_id = $1 AND projection_type = $2
        ORDER BY updated_at DESC, key ASC
        LIMIT $3
        "#,
    )
    .bind(user_id)
    .bind(projection_type)
    .bind(limit)
    .fetch_all(&mut **tx)
    .await?;

    Ok(rows.into_iter().map(|r| r.into_response(now)).collect())
}

fn clamp_verify_timeout_ms(value: Option<u64>) -> u64 {
    value.unwrap_or(1200).clamp(100, 10_000)
}

fn normalize_read_after_write_targets(
    targets: Vec<AgentReadAfterWriteTarget>,
) -> Vec<(String, String)> {
    let mut dedup = HashSet::new();
    let mut normalized = Vec::new();
    for target in targets {
        let projection_type = target.projection_type.trim().to_lowercase();
        let key = target.key.trim().to_lowercase();
        if projection_type.is_empty() || key.is_empty() {
            continue;
        }
        if dedup.insert((projection_type.clone(), key.clone())) {
            normalized.push((projection_type, key));
        }
    }
    normalized
}

const WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE: &str = "workflow.onboarding.closed";
const WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE: &str = "workflow.onboarding.override_granted";
const WORKFLOW_INVARIANT_ID: &str = "INV-004";
const ONBOARDING_REQUIRED_AREAS: [&str; 3] = [
    "training_background",
    "baseline_profile",
    "unit_preferences",
];
const PLANNING_OR_COACHING_EVENT_TYPES: [&str; 8] = [
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    "projection_rule.created",
    "projection_rule.archived",
    "weight_target.set",
    "sleep_target.set",
    "nutrition_target.set",
];
const VISUALIZATION_RENDER_FORMATS: [&str; 5] = ["chart", "table", "timeline", "ascii", "mermaid"];
const VISUALIZATION_INVARIANT_ID: &str = "INV-009";
const VISUALIZATION_TREND_KEYWORDS: [&str; 8] = [
    "trend",
    "progress",
    "verlauf",
    "entwicklung",
    "plateau",
    "stagnation",
    "improve",
    "improving",
];
const VISUALIZATION_COMPARE_KEYWORDS: [&str; 7] = [
    "compare",
    "vergleich",
    "versus",
    " vs ",
    "difference",
    "delta",
    "gegenueber",
];
const VISUALIZATION_PLAN_VS_ACTUAL_KEYWORDS: [&str; 8] = [
    "plan vs actual",
    "planned vs actual",
    "soll ist",
    "adherence",
    "compliance",
    "abweichung",
    "planabweichung",
    "target vs actual",
];
const VISUALIZATION_MULTI_WEEK_KEYWORDS: [&str; 8] = [
    "multi-week",
    "multi week",
    "mehrwoechig",
    "mehrwöchig",
    "weekly schedule",
    "wochenplan",
    "several weeks",
    "weeks ahead",
];

fn default_true() -> bool {
    true
}

fn is_planning_or_coaching_event_type(event_type: &str) -> bool {
    let normalized = event_type.trim().to_lowercase();
    PLANNING_OR_COACHING_EVENT_TYPES.contains(&normalized.as_str())
}

fn timezone_from_user_profile(data: &Value) -> Option<String> {
    let user = data.get("user").and_then(Value::as_object)?;
    let preferences = user.get("preferences").and_then(Value::as_object)?;
    for key in ["timezone", "time_zone"] {
        let configured = preferences
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or_default();
        if !configured.is_empty() {
            return Some(configured.to_string());
        }
    }
    None
}

fn has_timezone_preference_in_user_profile(data: &Value) -> bool {
    timezone_from_user_profile(data).is_some()
}

fn normalize_visualization_format(format: &str) -> Option<String> {
    let normalized = format.trim().to_lowercase();
    if VISUALIZATION_RENDER_FORMATS.contains(&normalized.as_str()) {
        Some(normalized)
    } else {
        None
    }
}

fn normalize_visualization_preference(preference: Option<&str>) -> String {
    match preference.unwrap_or("auto").trim().to_lowercase().as_str() {
        "always" => "always".to_string(),
        "never" => "never".to_string(),
        _ => "auto".to_string(),
    }
}

fn contains_any_keyword(haystack: &str, keywords: &[&str]) -> bool {
    keywords.iter().any(|keyword| haystack.contains(keyword))
}

fn detect_visualization_trigger(task_intent: &str) -> String {
    let normalized = format!(" {} ", task_intent.trim().to_lowercase());
    if contains_any_keyword(&normalized, &VISUALIZATION_PLAN_VS_ACTUAL_KEYWORDS) {
        return "plan_vs_actual".to_string();
    }
    if contains_any_keyword(&normalized, &VISUALIZATION_MULTI_WEEK_KEYWORDS) {
        return "multi_week_scheduling".to_string();
    }
    if contains_any_keyword(&normalized, &VISUALIZATION_COMPARE_KEYWORDS) {
        return "compare".to_string();
    }
    if contains_any_keyword(&normalized, &VISUALIZATION_TREND_KEYWORDS) {
        return "trend".to_string();
    }
    "none".to_string()
}

fn normalize_visualization_complexity(
    complexity_hint: Option<&str>,
    source_count: usize,
    trigger: &str,
) -> String {
    let normalized_hint = complexity_hint.unwrap_or("").trim().to_lowercase();
    if matches!(normalized_hint.as_str(), "low" | "medium" | "high") {
        return normalized_hint;
    }
    if source_count >= 3 || trigger == "plan_vs_actual" || trigger == "multi_week_scheduling" {
        return "high".to_string();
    }
    if source_count >= 2 || trigger == "trend" || trigger == "compare" {
        return "medium".to_string();
    }
    "low".to_string()
}

fn visualization_policy_decision(
    task_intent: &str,
    user_preference_override: Option<&str>,
    complexity_hint: Option<&str>,
    source_count: usize,
) -> AgentVisualizationPolicyDecision {
    let preference_mode = normalize_visualization_preference(user_preference_override);
    let trigger = detect_visualization_trigger(task_intent);
    let complexity = normalize_visualization_complexity(complexity_hint, source_count, &trigger);

    if preference_mode == "never" {
        return AgentVisualizationPolicyDecision {
            status: "skipped".to_string(),
            trigger: "user_preference_never".to_string(),
            preference_mode,
            complexity,
            reason: "Visualization skipped due to explicit user preference override.".to_string(),
        };
    }

    if preference_mode == "always" {
        return AgentVisualizationPolicyDecision {
            status: "visualize".to_string(),
            trigger: "user_preference_always".to_string(),
            preference_mode,
            complexity,
            reason: "Visualization enabled due to explicit user preference override.".to_string(),
        };
    }

    if trigger == "none" {
        return AgentVisualizationPolicyDecision {
            status: "skipped".to_string(),
            trigger,
            preference_mode,
            complexity,
            reason:
                "No visualization trigger detected (trend/compare/plan-vs-actual/multi-week scheduling)."
                    .to_string(),
        };
    }

    AgentVisualizationPolicyDecision {
        status: "visualize".to_string(),
        trigger,
        preference_mode,
        complexity,
        reason: "Visualization trigger detected and policy allows structured rendering."
            .to_string(),
    }
}

fn normalize_visualization_spec(
    spec: AgentVisualizationSpec,
) -> Result<AgentVisualizationSpec, AppError> {
    let format =
        normalize_visualization_format(&spec.format).ok_or_else(|| AppError::Validation {
            message: "visualization_spec.format is not supported".to_string(),
            field: Some("visualization_spec.format".to_string()),
            received: Some(serde_json::json!(spec.format)),
            docs_hint: Some(
                "Supported formats: chart, table, timeline, ascii, mermaid.".to_string(),
            ),
        })?;
    let purpose = spec.purpose.trim();
    if purpose.is_empty() {
        return Err(AppError::Validation {
            message: "visualization_spec.purpose must not be empty".to_string(),
            field: Some("visualization_spec.purpose".to_string()),
            received: None,
            docs_hint: Some(
                "Provide a concrete purpose such as '4-week volume trend' or 'plan-vs-actual adherence'."
                    .to_string(),
            ),
        });
    }
    if spec.data_sources.is_empty() {
        return Err(AppError::Validation {
            message: "visualization_spec.data_sources must not be empty".to_string(),
            field: Some("visualization_spec.data_sources".to_string()),
            received: None,
            docs_hint: Some(
                "Declare at least one projection source with projection_type/key before rendering."
                    .to_string(),
            ),
        });
    }

    let normalized_sources: Vec<AgentVisualizationDataSource> = spec
        .data_sources
        .iter()
        .enumerate()
        .map(|(index, source)| {
            let projection_type = source.projection_type.trim().to_lowercase();
            let key = source.key.trim().to_lowercase();
            if projection_type.is_empty() || key.is_empty() {
                return Err(AppError::Validation {
                    message: "Each visualization data source requires projection_type and key"
                        .to_string(),
                    field: Some(format!("visualization_spec.data_sources[{index}]")),
                    received: Some(serde_json::json!({
                        "projection_type": source.projection_type,
                        "key": source.key,
                    })),
                    docs_hint: Some(
                        "Use a concrete source reference, e.g. projection_type='training_timeline', key='overview'."
                            .to_string(),
                    ),
                });
            }
            let json_path = source
                .json_path
                .as_ref()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty());
            Ok(AgentVisualizationDataSource {
                projection_type,
                key,
                json_path,
            })
        })
        .collect::<Result<Vec<_>, AppError>>()?;

    Ok(AgentVisualizationSpec {
        format,
        purpose: purpose.to_string(),
        title: spec
            .title
            .as_ref()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty()),
        timezone: spec
            .timezone
            .as_ref()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty()),
        data_sources: normalized_sources,
    })
}

fn extract_json_path_value(data: &Value, json_path: Option<&str>) -> Option<Value> {
    let path = json_path.map(str::trim).unwrap_or_default();
    if path.is_empty() {
        return Some(data.clone());
    }

    let mut current = data;
    for segment in path.split('.') {
        let token = segment.trim();
        if token.is_empty() {
            return None;
        }
        if let Ok(index) = token.parse::<usize>() {
            current = current.as_array()?.get(index)?;
        } else {
            current = current.get(token)?;
        }
    }
    Some(current.clone())
}

fn bind_visualization_source(
    source: &AgentVisualizationDataSource,
    projection: &ProjectionResponse,
) -> Result<AgentVisualizationResolvedSource, String> {
    let value = extract_json_path_value(&projection.projection.data, source.json_path.as_deref())
        .ok_or_else(|| {
        format!(
            "{}:{} path '{}' was not resolvable",
            source.projection_type,
            source.key,
            source.json_path.as_deref().unwrap_or_default()
        )
    })?;

    Ok(AgentVisualizationResolvedSource {
        projection_type: source.projection_type.clone(),
        key: source.key.clone(),
        json_path: source.json_path.clone(),
        projection_version: projection.projection.version,
        projection_last_event_id: projection.projection.last_event_id,
        value,
    })
}

fn resolve_visualization_timezone_context(
    spec: &AgentVisualizationSpec,
    user_profile: Option<&ProjectionResponse>,
) -> AgentVisualizationTimezoneContext {
    if let Some(explicit) = spec
        .timezone
        .as_ref()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
    {
        return AgentVisualizationTimezoneContext {
            timezone: explicit,
            assumed: false,
            source: "spec".to_string(),
        };
    }

    if let Some(profile) = user_profile {
        if let Some(profile_timezone) = timezone_from_user_profile(&profile.projection.data) {
            return AgentVisualizationTimezoneContext {
                timezone: profile_timezone,
                assumed: false,
                source: "user_profile.preference".to_string(),
            };
        }
    }

    AgentVisualizationTimezoneContext {
        timezone: "UTC".to_string(),
        assumed: true,
        source: "fallback_utc".to_string(),
    }
}

fn visualization_uncertainty_label(quality_health: Option<&ProjectionResponse>) -> Option<String> {
    let status = quality_health?
        .projection
        .data
        .get("status")
        .and_then(Value::as_str)
        .map(str::trim)
        .unwrap_or_default()
        .to_lowercase();

    match status.as_str() {
        "degraded" => Some(
            "Data quality is currently degraded; treat this visualization as directional, not definitive."
                .to_string(),
        ),
        "monitor" => Some(
            "Data quality is in monitor mode; verify key conclusions before acting on the chart."
                .to_string(),
        ),
        _ => None,
    }
}

fn truncate_visualization_value(value: &Value) -> String {
    let serialized = serde_json::to_string(value).unwrap_or_else(|_| "null".to_string());
    if serialized.len() <= 180 {
        serialized
    } else {
        format!("{}...", &serialized[..177])
    }
}

fn build_visualization_equivalent_summary(
    spec: &AgentVisualizationSpec,
    resolved_sources: &[AgentVisualizationResolvedSource],
    timezone_context: &AgentVisualizationTimezoneContext,
    uncertainty_label: Option<&str>,
) -> String {
    let mut lines = vec![
        format!("Purpose: {}", spec.purpose),
        format!(
            "Timezone: {}{}",
            timezone_context.timezone,
            if timezone_context.assumed {
                " (assumed)"
            } else {
                ""
            }
        ),
    ];

    for source in resolved_sources {
        let path_suffix = source
            .json_path
            .as_ref()
            .map(|path| format!(".{path}"))
            .unwrap_or_default();
        lines.push(format!(
            "Source {}:{}{} => {}",
            source.projection_type,
            source.key,
            path_suffix,
            truncate_visualization_value(&source.value)
        ));
    }

    if let Some(label) = uncertainty_label {
        lines.push(format!("Uncertainty: {label}"));
    }

    lines.join("\n")
}

fn build_mermaid_preview(
    resolved_sources: &[AgentVisualizationResolvedSource],
    summary: &str,
) -> String {
    let mut mermaid_lines = vec!["flowchart TD".to_string()];
    for (index, source) in resolved_sources.iter().enumerate() {
        mermaid_lines.push(format!(
            "  S{index}[\"{}:{}\"]",
            source.projection_type, source.key
        ));
        if index > 0 {
            mermaid_lines.push(format!("  S{} --> S{index}", index - 1));
        }
    }
    format!("{}\n\n{}", mermaid_lines.join("\n"), summary)
}

fn build_visualization_outputs(
    spec: &AgentVisualizationSpec,
    resolved_sources: &[AgentVisualizationResolvedSource],
    timezone_context: &AgentVisualizationTimezoneContext,
    allow_rich_rendering: bool,
    uncertainty_label: Option<&str>,
) -> (
    String,
    AgentVisualizationOutput,
    Option<AgentVisualizationOutput>,
    Vec<String>,
) {
    let summary = build_visualization_equivalent_summary(
        spec,
        resolved_sources,
        timezone_context,
        uncertainty_label,
    );
    let mut warnings = Vec::new();
    if timezone_context.assumed {
        warnings.push(
            "Timezone was not explicitly configured; UTC fallback was used for visualization semantics."
                .to_string(),
        );
    }
    if let Some(label) = uncertainty_label {
        warnings.push(label.to_string());
    }

    if !allow_rich_rendering && spec.format != "ascii" {
        return (
            "fallback".to_string(),
            AgentVisualizationOutput {
                format: "ascii".to_string(),
                content: summary,
            },
            None,
            warnings,
        );
    }

    if spec.format == "ascii" {
        return (
            "visualize".to_string(),
            AgentVisualizationOutput {
                format: "ascii".to_string(),
                content: summary,
            },
            None,
            warnings,
        );
    }

    let rich_content = match spec.format.as_str() {
        "mermaid" => build_mermaid_preview(resolved_sources, &summary),
        "timeline" => format!("Timeline Preview\n\n{summary}"),
        "chart" => format!("Chart Preview\n\n{summary}"),
        "table" => format!("Table Preview\n\n{summary}"),
        _ => summary.clone(),
    };

    (
        "visualize".to_string(),
        AgentVisualizationOutput {
            format: spec.format.clone(),
            content: rich_content,
        },
        Some(AgentVisualizationOutput {
            format: "ascii".to_string(),
            content: summary,
        }),
        warnings,
    )
}

fn coverage_status_from_user_profile(data: &Value, area: &str) -> Option<String> {
    let coverage = data
        .get("user")
        .and_then(|u| u.get("interview_coverage"))
        .and_then(Value::as_array)?;
    coverage.iter().find_map(|entry| {
        let candidate_area = entry.get("area").and_then(Value::as_str)?.trim();
        if candidate_area != area {
            return None;
        }
        entry
            .get("status")
            .and_then(Value::as_str)
            .map(|status| status.trim().to_lowercase())
    })
}

fn missing_onboarding_close_requirements(user_profile: Option<&ProjectionResponse>) -> Vec<String> {
    let mut missing = Vec::new();
    let Some(profile) = user_profile else {
        missing.push("user_profile_missing".to_string());
        missing.push("user_profile_bootstrap_pending".to_string());
        return missing;
    };
    let data = &profile.projection.data;
    if data.get("user").map(Value::is_null).unwrap_or(true) {
        missing.push("user_profile_bootstrap_pending".to_string());
        return missing;
    }

    for area in ONBOARDING_REQUIRED_AREAS {
        let Some(status) = coverage_status_from_user_profile(data, area) else {
            missing.push(format!("coverage.{area}.missing"));
            continue;
        };
        let satisfied = if area == "baseline_profile" {
            matches!(status.as_str(), "covered" | "deferred")
        } else {
            status == "covered"
        };
        if !satisfied {
            missing.push(format!("coverage.{area}.{status}"));
        }
    }

    if !has_timezone_preference_in_user_profile(data) {
        missing.push("preference.timezone.missing".to_string());
    }

    missing
}

fn workflow_gate_from_request(
    events: &[CreateEventRequest],
    state: &AgentWorkflowState,
) -> AgentWorkflowGate {
    let planning_event_types: Vec<String> = events
        .iter()
        .filter_map(|event| {
            let event_type = event.event_type.trim().to_lowercase();
            if is_planning_or_coaching_event_type(&event_type) {
                Some(event_type)
            } else {
                None
            }
        })
        .collect();
    let contains_planning_action = !planning_event_types.is_empty();
    let requested_close = events.iter().any(|event| {
        event
            .event_type
            .trim()
            .eq_ignore_ascii_case(WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE)
    });
    let requested_override = events.iter().any(|event| {
        event
            .event_type
            .trim()
            .eq_ignore_ascii_case(WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE)
    });

    if !contains_planning_action {
        return AgentWorkflowGate {
            phase: if state.onboarding_closed {
                "planning".to_string()
            } else {
                "onboarding".to_string()
            },
            status: "allowed".to_string(),
            transition: "none".to_string(),
            onboarding_closed: state.onboarding_closed,
            override_used: false,
            message: if state.onboarding_closed {
                "Onboarding is closed; planning/coaching actions are available.".to_string()
            } else {
                "Onboarding remains active; no planning/coaching payload detected.".to_string()
            },
            missing_requirements: state.missing_close_requirements.clone(),
            planning_event_types,
        };
    }

    if state.onboarding_closed {
        return AgentWorkflowGate {
            phase: "planning".to_string(),
            status: "allowed".to_string(),
            transition: "none".to_string(),
            onboarding_closed: true,
            override_used: false,
            message: "Planning/coaching payload allowed because onboarding is already closed."
                .to_string(),
            missing_requirements: Vec::new(),
            planning_event_types,
        };
    }

    if requested_close && state.missing_close_requirements.is_empty() {
        return AgentWorkflowGate {
            phase: "planning".to_string(),
            status: "allowed".to_string(),
            transition: "onboarding_closed".to_string(),
            onboarding_closed: true,
            override_used: false,
            message:
                "Onboarding close transition accepted. Planning/coaching payload is now allowed."
                    .to_string(),
            missing_requirements: Vec::new(),
            planning_event_types,
        };
    }

    if requested_override || state.override_active {
        return AgentWorkflowGate {
            phase: "onboarding".to_string(),
            status: "allowed".to_string(),
            transition: "override".to_string(),
            onboarding_closed: false,
            override_used: true,
            message: "Planning/coaching payload allowed via explicit onboarding override."
                .to_string(),
            missing_requirements: state.missing_close_requirements.clone(),
            planning_event_types,
        };
    }

    if state.legacy_planning_history && state.missing_close_requirements.is_empty() {
        return AgentWorkflowGate {
            phase: "planning".to_string(),
            status: "allowed".to_string(),
            transition: "onboarding_closed".to_string(),
            onboarding_closed: true,
            override_used: false,
            message: "Planning/coaching payload allowed for legacy compatibility; onboarding close marker will be auto-recorded."
                .to_string(),
            missing_requirements: Vec::new(),
            planning_event_types,
        };
    }

    AgentWorkflowGate {
        phase: "onboarding".to_string(),
        status: "blocked".to_string(),
        transition: "none".to_string(),
        onboarding_closed: false,
        override_used: false,
        message: "Planning/coaching payload blocked: onboarding phase is not closed.".to_string(),
        missing_requirements: state.missing_close_requirements.clone(),
        planning_event_types,
    }
}

fn build_auto_onboarding_close_event(events: &[CreateEventRequest]) -> CreateEventRequest {
    let mut idempotency_keys: Vec<String> = events
        .iter()
        .map(|event| event.metadata.idempotency_key.trim().to_lowercase())
        .filter(|key| !key.is_empty())
        .collect();
    idempotency_keys.sort();
    idempotency_keys.dedup();
    let seed = format!("workflow_auto_close|{}", idempotency_keys.join("|"));
    let idempotency_key = format!("workflow-auto-close-{}", stable_hash_suffix(&seed, 20));
    let session_id = events
        .iter()
        .find_map(|event| event.metadata.session_id.clone())
        .filter(|value| !value.trim().is_empty())
        .or_else(|| Some("workflow:onboarding-auto-close".to_string()));

    CreateEventRequest {
        timestamp: Utc::now(),
        event_type: WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE.to_string(),
        data: serde_json::json!({
            "reason": "Auto-close emitted for legacy compatibility before planning/coaching write.",
            "closed_by": "system_auto",
            "compatibility_mode": "legacy_planning_history",
        }),
        metadata: EventMetadata {
            source: Some("agent_write_with_proof".to_string()),
            agent: Some("api".to_string()),
            device: None,
            session_id,
            idempotency_key,
        },
    }
}

const SESSION_AUDIT_MENTION_BOUND_FIELDS: [&str; 4] = ["rest_seconds", "tempo", "rir", "set_type"];
const SESSION_AUDIT_INVARIANT_ID: &str = "INV-008";
const EVIDENCE_PARSER_VERSION: &str = "mention_parser.v1";
const EVIDENCE_CLAIM_EVENT_TYPE: &str = "evidence.claim.logged";
const AUDIT_CLASS_MISSING_MENTION_FIELD: &str = "missing_mention_bound_field";
const AUDIT_CLASS_SCALE_NORMALIZED_TO_FIVE: &str = "scale_normalized_to_five";
const AUDIT_CLASS_SCALE_OUT_OF_BOUNDS: &str = "scale_out_of_bounds";
const AUDIT_CLASS_NARRATIVE_CONTRADICTION: &str = "narrative_structured_contradiction";
const AUDIT_CLASS_UNSUPPORTED_INFERRED: &str = "unsupported_inferred_value";
const SESSION_FEEDBACK_CERTAINTY_CONFIRMED: &str = "confirmed";
const SESSION_FEEDBACK_CERTAINTY_INFERRED: &str = "inferred";
const SESSION_FEEDBACK_CERTAINTY_UNRESOLVED: &str = "unresolved";
const SESSION_FEEDBACK_CONTEXT_KEYS: [&str; 6] = [
    "context",
    "context_text",
    "summary",
    "comment",
    "notes",
    "feeling",
];
const SESSION_POSITIVE_HINTS: [&str; 9] = [
    "good", "great", "fun", "strong", "solid", "leicht", "easy", "well", "locker",
];
const SESSION_NEGATIVE_HINTS: [&str; 10] = [
    "bad", "terrible", "schlecht", "pain", "hurt", "injury", "müde", "tired", "awful", "weak",
];
const SESSION_EASY_HINTS: [&str; 5] = ["easy", "leicht", "locker", "chill", "smooth"];
const SESSION_HARD_HINTS: [&str; 8] = [
    "hard",
    "brutal",
    "tough",
    "exhausting",
    "all-out",
    "maxed",
    "heavy",
    "grindy",
];

static TEMPO_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\btempo\s*[:=]?\s*(\d-[\dx]-[\dx]-[\dx])\b").expect("valid tempo regex")
});
static TEMPO_BARE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(\d-[\dx]-[\dx]-[\dx])\b").expect("valid tempo bare"));
static RIR_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?:rir\s*[:=]?\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*rir|(\d+)\s*reps?\s+in\s+reserve)\b",
    )
    .expect("valid rir regex")
});
static REST_MMSS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:rest|pause|break|satzpause)\s*[:=]?\s*(\d{1,2}):(\d{2})\b")
        .expect("valid rest mmss regex")
});
static REST_SECONDS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?:(?:rest|pause|break|satzpause)\s*[:=]?\s*(\d{1,3})\s*(?:s|sec|secs|second|seconds)|(\d{1,3})\s*(?:s|sec|secs|second|seconds)\s*(?:rest|pause|break|satzpause))\b",
    )
    .expect("valid rest seconds regex")
});
static REST_MINUTES_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?:(?:rest|pause|break|satzpause)\s*[:=]?\s*(\d{1,2})\s*(?:m|min|mins|minute|minutes)|(\d{1,2})\s*(?:m|min|mins|minute|minutes)\s*(?:rest|pause|break|satzpause))\b",
    )
    .expect("valid rest minutes regex")
});
static REST_NUMBER_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:rest|pause|break|satzpause)\s*[:=]?\s*(\d{1,3})\b")
        .expect("valid rest number regex")
});
static SET_TYPE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(warm[\s-]?up|back[\s-]?off|amrap|working)\b").expect("valid set type regex")
});

#[derive(Debug, Clone)]
struct MentionValueWithSpan {
    value: Value,
    unit: Option<String>,
    span_start: usize,
    span_end: usize,
    span_text: String,
    confidence: f64,
}

#[derive(Debug, Clone)]
struct EvidenceClaimDraft {
    claim_type: String,
    value: Value,
    unit: Option<String>,
    confidence: f64,
    source_field: String,
    source_text: String,
    span_start: usize,
    span_end: usize,
    span_text: String,
}

fn round_to_two(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn normalize_rest_seconds(value: f64) -> Option<f64> {
    if !value.is_finite() || value < 0.0 {
        return None;
    }
    Some(round_to_two(value))
}

fn normalize_rir(value: f64) -> Option<f64> {
    if !value.is_finite() {
        return None;
    }
    Some(round_to_two(value.clamp(0.0, 10.0)))
}

fn parse_rest_with_span(text: &str) -> Option<MentionValueWithSpan> {
    if let Some(caps) = REST_MMSS_RE.captures(text) {
        let minutes = caps.get(1)?.as_str().parse::<f64>().ok()?;
        let seconds = caps.get(2)?.as_str().parse::<f64>().ok()?;
        let value = normalize_rest_seconds((minutes * 60.0) + seconds)?;
        let full = caps.get(0)?;
        return Some(MentionValueWithSpan {
            value: mention_value_from_number(value)?,
            unit: Some("seconds".to_string()),
            span_start: full.start(),
            span_end: full.end(),
            span_text: full.as_str().to_string(),
            confidence: 0.95,
        });
    }
    if let Some(caps) = REST_SECONDS_RE.captures(text) {
        let raw = caps
            .get(1)
            .or_else(|| caps.get(2))
            .map(|m| m.as_str())
            .and_then(|raw| raw.parse::<f64>().ok())?;
        let value = normalize_rest_seconds(raw)?;
        let full = caps.get(0)?;
        return Some(MentionValueWithSpan {
            value: mention_value_from_number(value)?,
            unit: Some("seconds".to_string()),
            span_start: full.start(),
            span_end: full.end(),
            span_text: full.as_str().to_string(),
            confidence: 0.95,
        });
    }
    if let Some(caps) = REST_MINUTES_RE.captures(text) {
        let raw = caps
            .get(1)
            .or_else(|| caps.get(2))
            .map(|m| m.as_str())
            .and_then(|raw| raw.parse::<f64>().ok())?;
        let value = normalize_rest_seconds(raw * 60.0)?;
        let full = caps.get(0)?;
        return Some(MentionValueWithSpan {
            value: mention_value_from_number(value)?,
            unit: Some("seconds".to_string()),
            span_start: full.start(),
            span_end: full.end(),
            span_text: full.as_str().to_string(),
            confidence: 0.93,
        });
    }
    if let Some(caps) = REST_NUMBER_RE.captures(text) {
        let raw = caps.get(1)?.as_str().parse::<f64>().ok()?;
        let value = normalize_rest_seconds(raw)?;
        let full = caps.get(0)?;
        return Some(MentionValueWithSpan {
            value: mention_value_from_number(value)?,
            unit: Some("seconds".to_string()),
            span_start: full.start(),
            span_end: full.end(),
            span_text: full.as_str().to_string(),
            confidence: 0.9,
        });
    }
    None
}

fn parse_rest_seconds_from_text(text: &str) -> Option<f64> {
    parse_rest_with_span(text).and_then(|parsed| parsed.value.as_f64())
}

fn parse_rir_with_span(text: &str) -> Option<MentionValueWithSpan> {
    let caps = RIR_RE.captures(text)?;
    let raw = caps
        .get(1)
        .or_else(|| caps.get(2))
        .or_else(|| caps.get(3))
        .map(|m| m.as_str())?;
    let value = normalize_rir(raw.parse::<f64>().ok()?)?;
    let full = caps.get(0)?;
    Some(MentionValueWithSpan {
        value: mention_value_from_number(value)?,
        unit: Some("reps_in_reserve".to_string()),
        span_start: full.start(),
        span_end: full.end(),
        span_text: full.as_str().to_string(),
        confidence: 0.95,
    })
}

fn parse_rir_from_text(text: &str) -> Option<f64> {
    parse_rir_with_span(text).and_then(|parsed| parsed.value.as_f64())
}

fn parse_tempo_with_span(text: &str) -> Option<MentionValueWithSpan> {
    let caps = TEMPO_RE
        .captures(text)
        .or_else(|| TEMPO_BARE_RE.captures(text))?;
    let raw = caps.get(1)?.as_str().trim().to_lowercase();
    if raw.is_empty() {
        return None;
    }
    let full = caps.get(0)?;
    Some(MentionValueWithSpan {
        value: Value::String(raw),
        unit: None,
        span_start: full.start(),
        span_end: full.end(),
        span_text: full.as_str().to_string(),
        confidence: 0.95,
    })
}

fn parse_tempo_from_text(text: &str) -> Option<String> {
    parse_tempo_with_span(text).and_then(|parsed| parsed.value.as_str().map(str::to_string))
}

fn normalize_set_type(value: &str) -> Option<String> {
    let text = value.trim().to_lowercase();
    if text.is_empty() {
        return None;
    }
    for (needle, canonical) in [
        ("warmup", "warmup"),
        ("warm-up", "warmup"),
        ("backoff", "backoff"),
        ("back-off", "backoff"),
        ("amrap", "amrap"),
        ("working", "working"),
    ] {
        if text.contains(needle) {
            return Some(canonical.to_string());
        }
    }
    None
}

fn parse_set_type_with_span(text: &str) -> Option<MentionValueWithSpan> {
    let captures = SET_TYPE_RE.captures(text)?;
    let matched = captures.get(1)?;
    let canonical = normalize_set_type(matched.as_str())?;
    Some(MentionValueWithSpan {
        value: Value::String(canonical),
        unit: None,
        span_start: matched.start(),
        span_end: matched.end(),
        span_text: matched.as_str().to_string(),
        confidence: 0.9,
    })
}

fn mention_value_from_number(value: f64) -> Option<Value> {
    serde_json::Number::from_f64(value).map(Value::Number)
}

fn extract_set_context_mentions_from_text(text: &str) -> HashMap<&'static str, Value> {
    let mut mentions = HashMap::new();
    let normalized = text.trim().to_lowercase();
    if normalized.is_empty() {
        return mentions;
    }

    if let Some(value) =
        parse_rest_seconds_from_text(&normalized).and_then(mention_value_from_number)
    {
        mentions.insert("rest_seconds", value);
    }
    if let Some(value) = parse_rir_from_text(&normalized).and_then(mention_value_from_number) {
        mentions.insert("rir", value);
    }
    if let Some(value) = parse_tempo_from_text(&normalized) {
        mentions.insert("tempo", Value::String(value));
    }
    if let Some(value) = normalize_set_type(&normalized) {
        mentions.insert("set_type", Value::String(value));
    }

    mentions
}

fn event_text_candidates(event: &CreateEventRequest) -> Vec<&str> {
    event_text_candidates_with_source(event)
        .into_iter()
        .map(|(_, text)| text)
        .collect()
}

fn event_text_candidates_with_source(event: &CreateEventRequest) -> Vec<(&'static str, &str)> {
    let mut out = Vec::new();
    for key in ["notes", "context_text", "utterance"] {
        if let Some(text) = event.data.get(key).and_then(Value::as_str) {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                out.push((key, trimmed));
            }
        }
    }
    out
}

fn extract_evidence_claim_drafts(event: &CreateEventRequest) -> Vec<EvidenceClaimDraft> {
    let mut drafts = Vec::new();
    for (source_field, source_text) in event_text_candidates_with_source(event) {
        if let Some(parsed) = parse_rest_with_span(source_text) {
            drafts.push(EvidenceClaimDraft {
                claim_type: "set_context.rest_seconds".to_string(),
                value: parsed.value,
                unit: parsed.unit,
                confidence: parsed.confidence,
                source_field: source_field.to_string(),
                source_text: source_text.to_string(),
                span_start: parsed.span_start,
                span_end: parsed.span_end,
                span_text: parsed.span_text,
            });
        }
        if let Some(parsed) = parse_rir_with_span(source_text) {
            drafts.push(EvidenceClaimDraft {
                claim_type: "set_context.rir".to_string(),
                value: parsed.value,
                unit: parsed.unit,
                confidence: parsed.confidence,
                source_field: source_field.to_string(),
                source_text: source_text.to_string(),
                span_start: parsed.span_start,
                span_end: parsed.span_end,
                span_text: parsed.span_text,
            });
        }
        if let Some(parsed) = parse_tempo_with_span(source_text) {
            drafts.push(EvidenceClaimDraft {
                claim_type: "set_context.tempo".to_string(),
                value: parsed.value,
                unit: parsed.unit,
                confidence: parsed.confidence,
                source_field: source_field.to_string(),
                source_text: source_text.to_string(),
                span_start: parsed.span_start,
                span_end: parsed.span_end,
                span_text: parsed.span_text,
            });
        }
        if let Some(parsed) = parse_set_type_with_span(source_text) {
            drafts.push(EvidenceClaimDraft {
                claim_type: "set_context.set_type".to_string(),
                value: parsed.value,
                unit: parsed.unit,
                confidence: parsed.confidence,
                source_field: source_field.to_string(),
                source_text: source_text.to_string(),
                span_start: parsed.span_start,
                span_end: parsed.span_end,
                span_text: parsed.span_text,
            });
        }
    }
    drafts
}

fn evidence_scope_for_event(event: &CreateEventRequest) -> Value {
    let scope_level = if event.event_type.trim().eq_ignore_ascii_case("set.logged") {
        "set"
    } else {
        "session"
    };
    let session_id = event.metadata.session_id.clone();
    let exercise_id = event
        .data
        .get("exercise_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    serde_json::json!({
        "level": scope_level,
        "event_type": event.event_type,
        "session_id": session_id,
        "exercise_id": exercise_id,
    })
}

fn build_evidence_claim_events(
    user_id: Uuid,
    events: &[CreateEventRequest],
    receipts: &[AgentWriteReceipt],
) -> Vec<CreateEventRequest> {
    let mut claim_events = Vec::new();
    let mut seen_idempotency_keys: HashSet<String> = HashSet::new();

    for (index, event) in events.iter().enumerate() {
        let Some(receipt) = receipts.get(index) else {
            continue;
        };
        for draft in extract_evidence_claim_drafts(event) {
            let value_fingerprint = canonical_mention_value(&draft.value);
            let seed = format!(
                "{}|{}|{}|{}|{}|{}|{}|{}",
                user_id,
                receipt.event_id,
                draft.claim_type,
                value_fingerprint,
                draft.source_field,
                draft.span_start,
                draft.span_end,
                EVIDENCE_PARSER_VERSION
            );
            let claim_id = format!("claim_{}", stable_hash_suffix(&seed, 24));
            let idempotency_key = format!("evidence-claim-{claim_id}");
            if !seen_idempotency_keys.insert(idempotency_key.clone()) {
                continue;
            }

            claim_events.push(CreateEventRequest {
                timestamp: event.timestamp,
                event_type: EVIDENCE_CLAIM_EVENT_TYPE.to_string(),
                data: serde_json::json!({
                    "claim_id": claim_id,
                    "claim_type": draft.claim_type,
                    "value": draft.value,
                    "unit": draft.unit,
                    "scope": evidence_scope_for_event(event),
                    "confidence": draft.confidence,
                    "provenance": {
                        "source_field": draft.source_field,
                        "source_text": draft.source_text,
                        "source_text_span": {
                            "start": draft.span_start,
                            "end": draft.span_end,
                            "text": draft.span_text,
                        },
                        "parser_version": EVIDENCE_PARSER_VERSION,
                    },
                    "lineage": {
                        "event_id": receipt.event_id,
                        "event_type": receipt.event_type,
                        "lineage_type": "supports",
                    },
                }),
                metadata: EventMetadata {
                    source: Some("agent_write_with_proof".to_string()),
                    agent: Some("api".to_string()),
                    device: None,
                    session_id: event.metadata.session_id.clone(),
                    idempotency_key,
                },
            });
        }
    }

    claim_events
}

fn event_structured_field_present(event: &CreateEventRequest, field: &str) -> bool {
    event
        .data
        .get(field)
        .map(|value| !value.is_null())
        .unwrap_or(false)
}

fn canonical_mention_value(value: &Value) -> String {
    if let Some(number) = value.as_f64() {
        return format!("{:.2}", number);
    }
    value
        .as_str()
        .map(|s| s.trim().to_lowercase())
        .unwrap_or_else(|| value.to_string())
}

fn extract_session_feedback_context(event: &CreateEventRequest) -> Option<String> {
    for key in SESSION_FEEDBACK_CONTEXT_KEYS {
        if let Some(text) = event.data.get(key).and_then(Value::as_str) {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_lowercase());
            }
        }
    }
    None
}

fn extract_feedback_scale_value(event: &CreateEventRequest, field: &str) -> Option<f64> {
    event.data.get(field).and_then(Value::as_f64)
}

fn contains_any_hint(text: &str, hints: &[&str]) -> bool {
    hints.iter().any(|hint| text.contains(hint))
}

fn has_unsupported_inferred_value(event: &CreateEventRequest, field: &str) -> bool {
    let source_key = format!("{field}_source");
    let evidence_key = format!("{field}_evidence_claim_id");
    let is_inferred = event
        .data
        .get(source_key.as_str())
        .and_then(Value::as_str)
        .map(|value| value.eq_ignore_ascii_case("inferred"))
        .unwrap_or(false);
    if !is_inferred {
        return false;
    }
    event
        .data
        .get(evidence_key.as_str())
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .is_none()
}

fn parse_non_empty_lower_str(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| value.to_ascii_lowercase())
}

fn has_non_null_field(event: &CreateEventRequest, field: &str) -> bool {
    event
        .data
        .get(field)
        .map(|value| !value.is_null())
        .unwrap_or(false)
}

fn validate_session_feedback_certainty_contract(
    events: &[CreateEventRequest],
) -> Result<(), AppError> {
    for (index, event) in events.iter().enumerate() {
        if !event.event_type.eq_ignore_ascii_case("session.completed") {
            continue;
        }

        for field in ["enjoyment", "perceived_quality", "perceived_exertion"] {
            let state_key = format!("{field}_state");
            let source_key = format!("{field}_source");
            let evidence_key = format!("{field}_evidence_claim_id");
            let unresolved_reason_key = format!("{field}_unresolved_reason");
            let field_path = format!("events[{index}].data.{field}");
            let state = parse_non_empty_lower_str(event.data.get(state_key.as_str()));
            let source = parse_non_empty_lower_str(event.data.get(source_key.as_str()));
            let has_value = has_non_null_field(event, field);
            let has_evidence = parse_non_empty_lower_str(event.data.get(evidence_key.as_str()))
                .is_some();
            let has_unresolved_reason =
                parse_non_empty_lower_str(event.data.get(unresolved_reason_key.as_str())).is_some();

            if let Some(state_value) = state.as_deref() {
                if ![
                    SESSION_FEEDBACK_CERTAINTY_CONFIRMED,
                    SESSION_FEEDBACK_CERTAINTY_INFERRED,
                    SESSION_FEEDBACK_CERTAINTY_UNRESOLVED,
                ]
                .contains(&state_value)
                {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_certainty_invalid_state".to_string(),
                        message: format!(
                            "{field} has invalid certainty state '{state_value}'. Allowed: confirmed|inferred|unresolved"
                        ),
                        field: Some(format!("{field_path}_state")),
                        received: event.data.get(state_key.as_str()).cloned(),
                        docs_hint: Some(
                            "Set <field>_state to confirmed, inferred, or unresolved. "
                                .to_string(),
                        ),
                    });
                }
            }

            if let Some(source_value) = source.as_deref() {
                if !["explicit", "user_confirmed", "estimated", "inferred"]
                    .contains(&source_value)
                {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_source_invalid".to_string(),
                        message: format!(
                            "{field} has invalid source '{source_value}'. Allowed: explicit|user_confirmed|estimated|inferred"
                        ),
                        field: Some(format!("{field_path}_source")),
                        received: event.data.get(source_key.as_str()).cloned(),
                        docs_hint: Some(
                            "Use canonical source labels for session feedback provenance."
                                .to_string(),
                        ),
                    });
                }
            }

            if matches!(
                state.as_deref(),
                Some(SESSION_FEEDBACK_CERTAINTY_CONFIRMED)
            ) && !has_value
            {
                return Err(AppError::PolicyViolation {
                    code: "session_feedback_confirmed_missing_value".to_string(),
                    message: format!(
                        "{field} is marked confirmed but no value was provided."
                    ),
                    field: Some(field_path.clone()),
                    received: event.data.get(field).cloned(),
                    docs_hint: Some(
                        "When <field>_state=confirmed, provide the numeric <field> value."
                            .to_string(),
                    ),
                });
            }

            if matches!(
                state.as_deref(),
                Some(SESSION_FEEDBACK_CERTAINTY_INFERRED)
            ) || matches!(source.as_deref(), Some("inferred"))
            {
                if !has_value {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_inferred_missing_value".to_string(),
                        message: format!(
                            "{field} is marked inferred but no value was provided."
                        ),
                        field: Some(field_path.clone()),
                        received: event.data.get(field).cloned(),
                        docs_hint: Some(
                            "When certainty/source is inferred, include the inferred numeric value."
                                .to_string(),
                        ),
                    });
                }
                if !has_evidence {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_inferred_missing_evidence".to_string(),
                        message: format!(
                            "{field} is inferred but missing {field}_evidence_claim_id."
                        ),
                        field: Some(format!("{field_path}_evidence_claim_id")),
                        received: event.data.get(evidence_key.as_str()).cloned(),
                        docs_hint: Some(
                            "Inferred subjective values require a linked evidence claim id."
                                .to_string(),
                        ),
                    });
                }
            }

            if matches!(
                state.as_deref(),
                Some(SESSION_FEEDBACK_CERTAINTY_UNRESOLVED)
            ) {
                if has_value {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_unresolved_has_value".to_string(),
                        message: format!(
                            "{field} is marked unresolved but a numeric value was still provided."
                        ),
                        field: Some(field_path.clone()),
                        received: event.data.get(field).cloned(),
                        docs_hint: Some(
                            "Use unresolved state only when no value is persisted yet."
                                .to_string(),
                        ),
                    });
                }
                if !has_unresolved_reason {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_unresolved_missing_reason".to_string(),
                        message: format!(
                            "{field} is unresolved but {field}_unresolved_reason is missing."
                        ),
                        field: Some(format!("{field_path}_unresolved_reason")),
                        received: event.data.get(unresolved_reason_key.as_str()).cloned(),
                        docs_hint: Some(
                            "Provide a short unresolved reason so the agent can ask one precise follow-up question."
                                .to_string(),
                        ),
                    });
                }
            }
        }
    }

    Ok(())
}

fn audit_field_label(field: &str) -> &'static str {
    match field {
        "rest_seconds" => "Satzpause",
        "tempo" => "Tempo",
        "rir" => "RIR",
        "set_type" => "Satztyp",
        "enjoyment" => "Session-Freude",
        "perceived_quality" => "Session-Qualität",
        "perceived_exertion" => "Session-Anstrengung",
        _ => "Feld",
    }
}

fn format_value_for_question(value: &str) -> String {
    if let Ok(parsed) = value.parse::<f64>() {
        if (parsed.fract()).abs() < f64::EPSILON {
            return format!("{}", parsed as i64);
        }
        return format!("{parsed:.2}");
    }
    value.to_string()
}

fn exercise_label_for_event(event: &CreateEventRequest) -> String {
    for key in ["exercise_id", "exercise"] {
        if let Some(label) = event.data.get(key).and_then(Value::as_str) {
            let trimmed = label.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    "diesem Satz".to_string()
}

fn build_clarification_question(unresolved: &[SessionAuditUnresolved]) -> Option<String> {
    let first = unresolved.first()?;
    if first.candidates.len() > 1 {
        let values = first
            .candidates
            .iter()
            .map(|v| format_value_for_question(v))
            .collect::<Vec<_>>()
            .join(" oder ");
        return Some(format!(
            "Konflikt bei {}: {} = {}. Welcher Wert stimmt?",
            first.exercise_label,
            audit_field_label(&first.field),
            values
        ));
    }
    let value = first
        .candidates
        .first()
        .map(|v| format_value_for_question(v))
        .unwrap_or_else(|| "einen Wert".to_string());
    Some(format!(
        "Bitte bestätigen: {} bei {} = {}?",
        audit_field_label(&first.field),
        first.exercise_label,
        value
    ))
}

fn summarize_inferred_provenance(provenance: &Value) -> String {
    if let Some(text) = provenance
        .get("source_text_span")
        .and_then(Value::as_object)
        .and_then(|span| span.get("text"))
        .and_then(Value::as_str)
    {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }
    if let Some(text) = provenance.get("source_text").and_then(Value::as_str) {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }
    provenance
        .get("source_type")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| "provenance_not_available".to_string())
}

fn collect_reliability_inferred_facts(
    evidence_events: &[CreateEventRequest],
    repair_events: &[CreateEventRequest],
) -> Vec<AgentReliabilityInferredFact> {
    let mut facts = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();

    for event in evidence_events {
        if !event
            .event_type
            .eq_ignore_ascii_case(EVIDENCE_CLAIM_EVENT_TYPE)
        {
            continue;
        }
        let field = event
            .data
            .get("claim_type")
            .and_then(Value::as_str)
            .unwrap_or("unknown_claim")
            .trim()
            .to_string();
        if field.is_empty() {
            continue;
        }
        let confidence = event
            .data
            .get("confidence")
            .and_then(Value::as_f64)
            .unwrap_or(0.0)
            .clamp(0.0, 1.0);
        let provenance =
            summarize_inferred_provenance(event.data.get("provenance").unwrap_or(&Value::Null));
        let dedup_key = format!("evidence|{field}|{provenance}");
        if seen.insert(dedup_key) {
            facts.push(AgentReliabilityInferredFact {
                field,
                confidence,
                provenance,
            });
        }
    }

    for event in repair_events {
        if !event.event_type.eq_ignore_ascii_case("set.corrected")
            && !event.event_type.eq_ignore_ascii_case("session.completed")
        {
            continue;
        }

        let repair_provenance = event.data.get("repair_provenance").unwrap_or(&Value::Null);
        let source_type = repair_provenance
            .get("source_type")
            .and_then(Value::as_str)
            .unwrap_or("");
        if !source_type.eq_ignore_ascii_case("inferred") {
            continue;
        }
        let confidence = repair_provenance
            .get("confidence")
            .and_then(Value::as_f64)
            .unwrap_or(0.0)
            .clamp(0.0, 1.0);
        let provenance = repair_provenance
            .get("reason")
            .and_then(Value::as_str)
            .map(str::to_string)
            .or_else(|| {
                event
                    .data
                    .get("reason")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .unwrap_or_else(|| "repair_provenance_not_available".to_string());

        if event.event_type.eq_ignore_ascii_case("set.corrected") {
            let changed_fields = event
                .data
                .get("changed_fields")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            for field in changed_fields.keys() {
                let dedup_key = format!("repair|set.corrected.{field}|{provenance}");
                if seen.insert(dedup_key) {
                    facts.push(AgentReliabilityInferredFact {
                        field: format!("set.corrected.{field}"),
                        confidence,
                        provenance: provenance.clone(),
                    });
                }
            }
            continue;
        }

        let inferred_fields = event
            .data
            .as_object()
            .map(|obj| {
                obj.iter()
                    .filter_map(|(key, value)| {
                        if !key.ends_with("_source")
                            || !value
                                .as_str()
                                .map(|source| source.eq_ignore_ascii_case("inferred"))
                                .unwrap_or(false)
                        {
                            return None;
                        }
                        Some(key.trim_end_matches("_source").to_string())
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        for field in inferred_fields {
            let dedup_key = format!("repair|session.completed.{field}|{provenance}");
            if seen.insert(dedup_key) {
                facts.push(AgentReliabilityInferredFact {
                    field: format!("session.completed.{field}"),
                    confidence,
                    provenance: provenance.clone(),
                });
            }
        }
    }

    facts
}

fn build_reliability_ux(
    claim_guard: &AgentWriteClaimGuard,
    session_audit: &AgentSessionAuditSummary,
    inferred_facts: Vec<AgentReliabilityInferredFact>,
) -> AgentReliabilityUx {
    if !claim_guard.allow_saved_claim || session_audit.status == "needs_clarification" {
        let assistant_phrase = if let Some(question) =
            session_audit.clarification_question.as_deref()
        {
            format!("Unresolved: Es gibt einen Konflikt. {}", question.trim())
        } else if claim_guard.claim_status == "failed" {
            "Unresolved: Write-Proof ist unvollständig; bitte erneut mit denselben Idempotency-Keys versuchen.".to_string()
        } else {
            "Unresolved: Verifikation läuft noch; bitte noch keinen finalen 'saved'-Claim machen."
                .to_string()
        };
        return AgentReliabilityUx {
            state: "unresolved".to_string(),
            assistant_phrase,
            inferred_facts,
            clarification_question: session_audit.clarification_question.clone(),
        };
    }

    if !inferred_facts.is_empty() || session_audit.status == "repaired" {
        let assistant_phrase = inferred_facts
            .first()
            .map(|item| {
                format!(
                    "Inferred: Speicherung ist verifiziert, aber mindestens ein Wert ist inferiert ({} @ {:.2}, Quelle: {}).",
                    item.field,
                    item.confidence,
                    item.provenance
                )
            })
            .unwrap_or_else(|| {
                "Inferred: Speicherung ist verifiziert, enthält aber inferierte Audit-Reparaturen mit Provenance."
                    .to_string()
            });
        return AgentReliabilityUx {
            state: "inferred".to_string(),
            assistant_phrase,
            inferred_facts,
            clarification_question: None,
        };
    }

    AgentReliabilityUx {
        state: "saved".to_string(),
        assistant_phrase: "Saved: Speicherung ist verifiziert (Receipt + Read-after-Write)."
            .to_string(),
        inferred_facts,
        clarification_question: None,
    }
}

fn all_read_after_write_verified(checks: &[AgentReadAfterWriteCheck]) -> bool {
    checks.iter().all(|check| check.status == "verified")
}

fn default_autonomy_policy() -> AgentAutonomyPolicy {
    let mut templates = HashMap::new();
    templates.insert(
        "non_trivial_action".to_string(),
        "Wenn du willst, kann ich als nächsten Schritt direkt fortfahren.".to_string(),
    );
    templates.insert(
        "plan_update".to_string(),
        "Wenn du willst, passe ich den Plan jetzt entsprechend an.".to_string(),
    );
    templates.insert(
        "repair_action".to_string(),
        "Eine risikoarme Reparatur ist möglich. Soll ich sie ausführen?".to_string(),
    );
    templates.insert(
        "post_save_followup".to_string(),
        "Speichern ist verifiziert.".to_string(),
    );

    AgentAutonomyPolicy {
        policy_version: "phase_3_integrity_slo_v1".to_string(),
        slo_status: "healthy".to_string(),
        calibration_status: "healthy".to_string(),
        throttle_active: false,
        max_scope_level: "moderate".to_string(),
        require_confirmation_for_non_trivial_actions: false,
        require_confirmation_for_plan_updates: false,
        require_confirmation_for_repairs: false,
        repair_auto_apply_enabled: true,
        reason: "No quality_health autonomy policy available; using healthy defaults.".to_string(),
        confirmation_templates: templates,
    }
}

fn parse_confirmation_templates(
    policy: &serde_json::Map<String, Value>,
) -> HashMap<String, String> {
    let mut templates = default_autonomy_policy().confirmation_templates;
    if let Some(custom) = policy
        .get("confirmation_templates")
        .and_then(Value::as_object)
    {
        for (key, value) in custom {
            if let Some(text) = value.as_str() {
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    templates.insert(key.to_string(), trimmed.to_string());
                }
            }
        }
    }
    templates
}

fn autonomy_policy_from_quality_health(
    quality_health: Option<&ProjectionResponse>,
) -> AgentAutonomyPolicy {
    let Some(projection) = quality_health else {
        return default_autonomy_policy();
    };
    let Some(policy) = projection
        .projection
        .data
        .get("autonomy_policy")
        .and_then(Value::as_object)
    else {
        return default_autonomy_policy();
    };

    AgentAutonomyPolicy {
        policy_version: policy
            .get("policy_version")
            .and_then(Value::as_str)
            .unwrap_or("phase_3_integrity_slo_v1")
            .to_string(),
        slo_status: policy
            .get("slo_status")
            .and_then(Value::as_str)
            .unwrap_or("healthy")
            .to_string(),
        calibration_status: policy
            .get("calibration_status")
            .and_then(Value::as_str)
            .unwrap_or("healthy")
            .to_string(),
        throttle_active: policy
            .get("throttle_active")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        max_scope_level: policy
            .get("max_scope_level")
            .and_then(Value::as_str)
            .unwrap_or("moderate")
            .to_string(),
        require_confirmation_for_non_trivial_actions: policy
            .get("require_confirmation_for_non_trivial_actions")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        require_confirmation_for_plan_updates: policy
            .get("require_confirmation_for_plan_updates")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        require_confirmation_for_repairs: policy
            .get("require_confirmation_for_repairs")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        repair_auto_apply_enabled: policy
            .get("repair_auto_apply_enabled")
            .and_then(Value::as_bool)
            .unwrap_or(true),
        reason: policy
            .get("reason")
            .and_then(Value::as_str)
            .unwrap_or("Autonomy policy derived from quality_health.")
            .to_string(),
        confirmation_templates: parse_confirmation_templates(policy),
    }
}

fn build_claim_guard(
    receipts: &[AgentWriteReceipt],
    requested_event_count: usize,
    checks: &[AgentReadAfterWriteCheck],
    warnings: &[BatchEventWarning],
    autonomy_policy: AgentAutonomyPolicy,
) -> AgentWriteClaimGuard {
    let mut uncertainty_markers = Vec::new();
    let mut deferred_markers = Vec::new();

    let receipts_complete = receipts.len() == requested_event_count
        && receipts
            .iter()
            .all(|r| !r.idempotency_key.trim().is_empty());
    if !receipts_complete {
        uncertainty_markers.push("write_receipt_incomplete".to_string());
        deferred_markers.push("defer_saved_claim_until_receipt_complete".to_string());
    }

    let read_after_write_ok = all_read_after_write_verified(checks);
    if !read_after_write_ok {
        uncertainty_markers.push("read_after_write_unverified".to_string());
        deferred_markers.push("defer_saved_claim_until_projection_readback".to_string());
    }

    if !warnings.is_empty() {
        uncertainty_markers.push("plausibility_warnings_present".to_string());
    }

    if autonomy_policy.throttle_active {
        uncertainty_markers.push("autonomy_throttled_by_integrity_slo".to_string());
        deferred_markers.push("confirm_non_trivial_actions_due_to_slo_regression".to_string());
    }

    let next_action_confirmation_prompt = if autonomy_policy.throttle_active {
        autonomy_policy
            .confirmation_templates
            .get("non_trivial_action")
            .cloned()
    } else {
        None
    };

    let allow_saved_claim = receipts_complete && read_after_write_ok;
    let (claim_status, recommended_user_phrase) = if allow_saved_claim
        && autonomy_policy.throttle_active
    {
        (
            "saved_verified".to_string(),
            autonomy_policy
                .confirmation_templates
                .get("post_save_followup")
                .cloned()
                .unwrap_or_else(|| {
                    format!(
                        "Saved and verified in the read model. Integrity status '{}' requires explicit confirmation before non-trivial follow-up actions.",
                        autonomy_policy.slo_status
                    )
                }),
        )
    } else if allow_saved_claim {
        (
            "saved_verified".to_string(),
            "Saved and verified in the read model.".to_string(),
        )
    } else if !receipts_complete {
        (
            "failed".to_string(),
            "Write proof incomplete (missing durable receipts). Avoid a saved claim and retry with the same idempotency keys.".to_string(),
        )
    } else {
        (
            "pending".to_string(),
            "Write accepted; verification still pending, so avoid a definitive 'saved' claim."
                .to_string(),
        )
    };

    AgentWriteClaimGuard {
        allow_saved_claim,
        claim_status,
        uncertainty_markers,
        deferred_markers,
        recommended_user_phrase,
        next_action_confirmation_prompt,
        autonomy_policy,
    }
}

fn build_save_claim_checked_event(
    requested_event_count: usize,
    receipts: &[AgentWriteReceipt],
    verification: &AgentWriteVerificationSummary,
    claim_guard: &AgentWriteClaimGuard,
    session_audit: &AgentSessionAuditSummary,
) -> CreateEventRequest {
    let mismatch_detected = !claim_guard.allow_saved_claim;
    let event_data = serde_json::json!({
        "requested_event_count": requested_event_count,
        "receipt_count": receipts.len(),
        "allow_saved_claim": claim_guard.allow_saved_claim,
        "claim_status": claim_guard.claim_status,
        "verification_status": verification.status,
        "write_path": verification.write_path,
        "required_checks": verification.required_checks,
        "verified_checks": verification.verified_checks,
        "mismatch_detected": mismatch_detected,
        "next_action_confirmation_prompt": claim_guard.next_action_confirmation_prompt,
        "uncertainty_markers": claim_guard.uncertainty_markers,
        "deferred_markers": claim_guard.deferred_markers,
        "autonomy_policy": {
            "slo_status": claim_guard.autonomy_policy.slo_status,
            "throttle_active": claim_guard.autonomy_policy.throttle_active,
            "max_scope_level": claim_guard.autonomy_policy.max_scope_level,
        },
        "session_audit": {
            "status": session_audit.status,
            "mismatch_detected": session_audit.mismatch_detected,
            "mismatch_repaired": session_audit.mismatch_repaired,
            "mismatch_unresolved": session_audit.mismatch_unresolved,
            "mismatch_classes": session_audit.mismatch_classes,
            "clarification_question": session_audit.clarification_question,
        },
    });

    CreateEventRequest {
        timestamp: Utc::now(),
        event_type: "quality.save_claim.checked".to_string(),
        data: event_data,
        metadata: EventMetadata {
            source: Some("agent_write_with_proof".to_string()),
            agent: Some("api".to_string()),
            device: None,
            session_id: Some("quality:save-claim".to_string()),
            idempotency_key: format!("quality-save-claim-checked-{}", Uuid::now_v7()),
        },
    }
}

const LEARNING_TELEMETRY_SCHEMA_VERSION: i64 = 1;
const SAVE_HANDSHAKE_INVARIANT_ID: &str = "INV-002";

fn stable_hash_suffix(seed: &str, chars: usize) -> String {
    let mut hasher = Sha256::new();
    hasher.update(seed.as_bytes());
    let digest = hex::encode(hasher.finalize());
    let end = chars.min(digest.len());
    digest[..end].to_string()
}

fn pseudonymize_user_id_for_learning_signal(user_id: Uuid) -> String {
    let salt = std::env::var("KURA_TELEMETRY_SALT")
        .unwrap_or_else(|_| "kura-learning-telemetry-v1".to_string());
    let seed = format!("{salt}:{user_id}");
    format!("u_{}", stable_hash_suffix(&seed, 24))
}

fn learning_signal_category(signal_type: &str) -> &'static str {
    match signal_type {
        "save_handshake_verified" => "outcome_signal",
        "save_handshake_pending" | "save_claim_mismatch_attempt" => "friction_signal",
        "workflow_violation" => "friction_signal",
        "workflow_override_used" => "correction_signal",
        "workflow_phase_transition_closed" => "outcome_signal",
        "viz_shown" => "outcome_signal",
        "viz_skipped" => "outcome_signal",
        "viz_source_bound" => "quality_signal",
        "viz_fallback_used" => "friction_signal",
        "viz_confusion_signal" => "friction_signal",
        "mismatch_detected" => "quality_signal",
        "mismatch_repaired" => "correction_signal",
        "mismatch_unresolved" => "friction_signal",
        _ => "quality_signal",
    }
}

fn save_claim_confidence_band(claim_guard: &AgentWriteClaimGuard) -> &'static str {
    if claim_guard.allow_saved_claim {
        "high"
    } else if claim_guard
        .uncertainty_markers
        .iter()
        .any(|marker| marker == "read_after_write_unverified")
    {
        "medium"
    } else {
        "low"
    }
}

fn build_learning_signal_event(
    user_id: Uuid,
    signal_type: &str,
    issue_type: &str,
    claim_guard: &AgentWriteClaimGuard,
    verification: &AgentWriteVerificationSummary,
    requested_event_count: usize,
    receipt_count: usize,
) -> CreateEventRequest {
    let captured_at = Utc::now();
    let confidence_band = save_claim_confidence_band(claim_guard);
    let agent_version =
        std::env::var("KURA_AGENT_VERSION").unwrap_or_else(|_| "api_agent_v1".to_string());
    let signature_seed = format!(
        "{}|{}|{}|{}|{}|{}|{}",
        signal_type,
        issue_type,
        SAVE_HANDSHAKE_INVARIANT_ID,
        agent_version,
        "agent_write_with_proof",
        "chat",
        confidence_band
    );
    let cluster_signature = format!("ls_{}", stable_hash_suffix(&signature_seed, 20));
    let event_data = serde_json::json!({
        "schema_version": LEARNING_TELEMETRY_SCHEMA_VERSION,
        "signal_type": signal_type,
        "category": learning_signal_category(signal_type),
        "captured_at": captured_at,
        "user_ref": {
            "pseudonymized_user_id": pseudonymize_user_id_for_learning_signal(user_id),
        },
        "signature": {
            "issue_type": issue_type,
            "invariant_id": SAVE_HANDSHAKE_INVARIANT_ID,
            "agent_version": agent_version,
            "workflow_phase": "agent_write_with_proof",
            "modality": "chat",
            "confidence_band": confidence_band,
        },
        "cluster_signature": cluster_signature,
        "attributes": {
            "requested_event_count": requested_event_count,
            "receipt_count": receipt_count,
            "allow_saved_claim": claim_guard.allow_saved_claim,
            "claim_status": claim_guard.claim_status,
            "verification_status": verification.status,
            "write_path": verification.write_path,
            "required_checks": verification.required_checks,
            "verified_checks": verification.verified_checks,
            "mismatch_detected": !claim_guard.allow_saved_claim,
        },
    });

    CreateEventRequest {
        timestamp: captured_at,
        event_type: "learning.signal.logged".to_string(),
        data: event_data,
        metadata: EventMetadata {
            source: Some("agent_write_with_proof".to_string()),
            agent: Some("api".to_string()),
            device: None,
            session_id: Some("learning:save-handshake".to_string()),
            idempotency_key: format!("learning-signal-{}", Uuid::now_v7()),
        },
    }
}

fn build_save_handshake_learning_signal_events(
    user_id: Uuid,
    requested_event_count: usize,
    receipts: &[AgentWriteReceipt],
    verification: &AgentWriteVerificationSummary,
    claim_guard: &AgentWriteClaimGuard,
) -> Vec<CreateEventRequest> {
    if claim_guard.allow_saved_claim {
        return vec![build_learning_signal_event(
            user_id,
            "save_handshake_verified",
            "save_handshake_verified",
            claim_guard,
            verification,
            requested_event_count,
            receipts.len(),
        )];
    }

    vec![
        build_learning_signal_event(
            user_id,
            "save_handshake_pending",
            "save_handshake_pending",
            claim_guard,
            verification,
            requested_event_count,
            receipts.len(),
        ),
        build_learning_signal_event(
            user_id,
            "save_claim_mismatch_attempt",
            "save_claim_mismatch_attempt",
            claim_guard,
            verification,
            requested_event_count,
            receipts.len(),
        ),
    ]
}

fn workflow_gate_signal_type(gate: &AgentWorkflowGate) -> Option<&'static str> {
    if gate.status == "blocked" {
        return Some("workflow_violation");
    }
    match gate.transition.as_str() {
        "onboarding_closed" => Some("workflow_phase_transition_closed"),
        "override" => Some("workflow_override_used"),
        _ => None,
    }
}

fn workflow_gate_confidence_band(gate: &AgentWorkflowGate) -> &'static str {
    if gate.status == "blocked" {
        "high"
    } else if gate.transition == "override" {
        "medium"
    } else {
        "high"
    }
}

fn build_workflow_gate_learning_signal_event(
    user_id: Uuid,
    gate: &AgentWorkflowGate,
) -> Option<CreateEventRequest> {
    let signal_type = workflow_gate_signal_type(gate)?;
    let captured_at = Utc::now();
    let confidence_band = workflow_gate_confidence_band(gate);
    let agent_version =
        std::env::var("KURA_AGENT_VERSION").unwrap_or_else(|_| "api_agent_v1".to_string());
    let signature_seed = format!(
        "{}|{}|{}|{}|{}|{}|{}",
        signal_type,
        "onboarding_phase_gate",
        WORKFLOW_INVARIANT_ID,
        agent_version,
        "onboarding_state_gate",
        "chat",
        confidence_band
    );
    let cluster_signature = format!("ls_{}", stable_hash_suffix(&signature_seed, 20));
    let event_data = serde_json::json!({
        "schema_version": LEARNING_TELEMETRY_SCHEMA_VERSION,
        "signal_type": signal_type,
        "category": learning_signal_category(signal_type),
        "captured_at": captured_at,
        "user_ref": {
            "pseudonymized_user_id": pseudonymize_user_id_for_learning_signal(user_id),
        },
        "signature": {
            "issue_type": "onboarding_phase_gate",
            "invariant_id": WORKFLOW_INVARIANT_ID,
            "agent_version": agent_version,
            "workflow_phase": "onboarding_state_gate",
            "modality": "chat",
            "confidence_band": confidence_band,
        },
        "cluster_signature": cluster_signature,
        "attributes": {
            "phase": gate.phase,
            "gate_status": gate.status,
            "transition": gate.transition,
            "onboarding_closed": gate.onboarding_closed,
            "override_used": gate.override_used,
            "missing_requirements": gate.missing_requirements,
            "planning_event_types": gate.planning_event_types,
        },
    });

    Some(CreateEventRequest {
        timestamp: captured_at,
        event_type: "learning.signal.logged".to_string(),
        data: event_data,
        metadata: EventMetadata {
            source: Some("agent_write_with_proof".to_string()),
            agent: Some("api".to_string()),
            device: None,
            session_id: Some("learning:onboarding-state-gate".to_string()),
            idempotency_key: format!("learning-signal-{}", Uuid::now_v7()),
        },
    })
}

fn build_visualization_learning_signal_event(
    user_id: Uuid,
    signal_type: &'static str,
    policy: &AgentVisualizationPolicyDecision,
    spec: Option<&AgentVisualizationSpec>,
    resolved_sources: &[AgentVisualizationResolvedSource],
    timezone_context: Option<&AgentVisualizationTimezoneContext>,
    uncertainty_label: Option<&str>,
    telemetry_session_id: Option<&str>,
) -> CreateEventRequest {
    let captured_at = Utc::now();
    let confidence_band = match signal_type {
        "viz_confusion_signal" => "medium",
        "viz_fallback_used" => "medium",
        _ => "high",
    };
    let agent_version =
        std::env::var("KURA_AGENT_VERSION").unwrap_or_else(|_| "api_agent_v1".to_string());
    let signature_seed = format!(
        "{}|{}|{}|{}|{}|{}|{}",
        signal_type,
        "visualization_policy",
        VISUALIZATION_INVARIANT_ID,
        agent_version,
        "visualization_resolve",
        "chat",
        confidence_band
    );
    let cluster_signature = format!("ls_{}", stable_hash_suffix(&signature_seed, 20));

    let event_data = serde_json::json!({
        "schema_version": LEARNING_TELEMETRY_SCHEMA_VERSION,
        "signal_type": signal_type,
        "category": learning_signal_category(signal_type),
        "captured_at": captured_at,
        "user_ref": {
            "pseudonymized_user_id": pseudonymize_user_id_for_learning_signal(user_id),
        },
        "signature": {
            "issue_type": "visualization_policy",
            "invariant_id": VISUALIZATION_INVARIANT_ID,
            "agent_version": agent_version,
            "workflow_phase": "visualization_resolve",
            "modality": "chat",
            "confidence_band": confidence_band,
        },
        "cluster_signature": cluster_signature,
        "attributes": {
            "policy_status": policy.status,
            "policy_trigger": policy.trigger,
            "policy_preference_mode": policy.preference_mode,
            "policy_complexity": policy.complexity,
            "visualization_format": spec.map(|s| s.format.clone()),
            "data_source_count": resolved_sources.len(),
            "data_sources": resolved_sources
                .iter()
                .map(|source| format!("{}:{}", source.projection_type, source.key))
                .collect::<Vec<_>>(),
            "timezone": timezone_context.map(|tz| tz.timezone.clone()),
            "timezone_assumed": timezone_context.map(|tz| tz.assumed),
            "uncertainty_label": uncertainty_label,
        },
    });

    CreateEventRequest {
        timestamp: captured_at,
        event_type: "learning.signal.logged".to_string(),
        data: event_data,
        metadata: EventMetadata {
            source: Some("agent_visualization_resolve".to_string()),
            agent: Some("api".to_string()),
            device: None,
            session_id: telemetry_session_id
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .or_else(|| Some("learning:visualization-policy".to_string())),
            idempotency_key: format!("learning-signal-{}", Uuid::now_v7()),
        },
    }
}

fn build_visualization_learning_signal_events(
    user_id: Uuid,
    policy: &AgentVisualizationPolicyDecision,
    spec: Option<&AgentVisualizationSpec>,
    resolved_sources: &[AgentVisualizationResolvedSource],
    timezone_context: Option<&AgentVisualizationTimezoneContext>,
    uncertainty_label: Option<&str>,
    telemetry_session_id: Option<&str>,
) -> Vec<CreateEventRequest> {
    let mut signal_types: Vec<&'static str> = Vec::new();
    if policy.status == "skipped" {
        signal_types.push("viz_skipped");
    } else {
        signal_types.push("viz_source_bound");
        if policy.status == "fallback" {
            signal_types.push("viz_fallback_used");
        } else {
            signal_types.push("viz_shown");
        }
    }
    if uncertainty_label.is_some() {
        signal_types.push("viz_confusion_signal");
    }

    signal_types
        .into_iter()
        .map(|signal_type| {
            build_visualization_learning_signal_event(
                user_id,
                signal_type,
                policy,
                spec,
                resolved_sources,
                timezone_context,
                uncertainty_label,
                telemetry_session_id,
            )
        })
        .collect()
}

fn session_audit_auto_repair_allowed(policy: &AgentAutonomyPolicy) -> bool {
    if policy.calibration_status.eq_ignore_ascii_case("degraded") {
        return false;
    }
    policy.repair_auto_apply_enabled && !policy.require_confirmation_for_repairs
}

fn session_audit_signal_confidence_band(
    signal_type: &str,
    summary: &AgentSessionAuditSummary,
) -> &'static str {
    match signal_type {
        "mismatch_repaired" => "high",
        "mismatch_unresolved" => "low",
        "mismatch_detected" if summary.mismatch_unresolved > 0 => "medium",
        _ => "high",
    }
}

fn build_session_audit_learning_signal_event(
    user_id: Uuid,
    signal_type: &str,
    summary: &AgentSessionAuditSummary,
) -> CreateEventRequest {
    let captured_at = Utc::now();
    let confidence_band = session_audit_signal_confidence_band(signal_type, summary);
    let agent_version =
        std::env::var("KURA_AGENT_VERSION").unwrap_or_else(|_| "api_agent_v1".to_string());
    let signature_seed = format!(
        "{}|{}|{}|{}|{}|{}|{}",
        signal_type,
        signal_type,
        SESSION_AUDIT_INVARIANT_ID,
        agent_version,
        "session_audit_gate",
        "chat",
        confidence_band
    );
    let cluster_signature = format!("ls_{}", stable_hash_suffix(&signature_seed, 20));

    let event_data = serde_json::json!({
        "schema_version": LEARNING_TELEMETRY_SCHEMA_VERSION,
        "signal_type": signal_type,
        "category": learning_signal_category(signal_type),
        "captured_at": captured_at,
        "user_ref": {
            "pseudonymized_user_id": pseudonymize_user_id_for_learning_signal(user_id),
        },
        "signature": {
            "issue_type": signal_type,
            "invariant_id": SESSION_AUDIT_INVARIANT_ID,
            "agent_version": agent_version,
            "workflow_phase": "session_audit_gate",
            "modality": "chat",
            "confidence_band": confidence_band,
        },
        "cluster_signature": cluster_signature,
        "attributes": {
            "audit_status": summary.status,
            "mismatch_detected": summary.mismatch_detected,
            "mismatch_repaired": summary.mismatch_repaired,
            "mismatch_unresolved": summary.mismatch_unresolved,
            "mismatch_classes": summary.mismatch_classes,
            "clarification_needed": summary.clarification_question.is_some(),
        },
    });

    CreateEventRequest {
        timestamp: captured_at,
        event_type: "learning.signal.logged".to_string(),
        data: event_data,
        metadata: EventMetadata {
            source: Some("agent_write_with_proof".to_string()),
            agent: Some("api".to_string()),
            device: None,
            session_id: Some("learning:session-audit".to_string()),
            idempotency_key: format!("learning-signal-{}", Uuid::now_v7()),
        },
    }
}

fn build_session_audit_artifacts(
    user_id: Uuid,
    requested_events: &[CreateEventRequest],
    requested_receipts: &[AgentWriteReceipt],
    autonomy_policy: &AgentAutonomyPolicy,
) -> SessionAuditArtifacts {
    let auto_repair_allowed = session_audit_auto_repair_allowed(autonomy_policy);
    let mut mismatch_detected = 0usize;
    let mut mismatch_repaired = 0usize;
    let mut mismatch_unresolved = 0usize;
    let mut mismatch_classes: BTreeMap<String, ()> = BTreeMap::new();
    let mut unresolved: Vec<SessionAuditUnresolved> = Vec::new();
    let mut repair_fields_by_target: BTreeMap<Uuid, BTreeMap<String, Value>> = BTreeMap::new();
    let mut session_id_by_target: HashMap<Uuid, Option<String>> = HashMap::new();
    let mut session_feedback_repair_events: Vec<CreateEventRequest> = Vec::new();

    for (index, event) in requested_events.iter().enumerate() {
        let event_type = event.event_type.trim().to_lowercase();
        let Some(receipt) = requested_receipts.get(index) else {
            continue;
        };

        if event_type == "set.logged" {
            let mut mentions_by_field: HashMap<String, BTreeMap<String, Value>> = HashMap::new();
            for text in event_text_candidates(event) {
                for (field, value) in extract_set_context_mentions_from_text(text) {
                    let canonical = canonical_mention_value(&value);
                    mentions_by_field
                        .entry(field.to_string())
                        .or_default()
                        .entry(canonical)
                        .or_insert(value);
                }
            }

            for field in SESSION_AUDIT_MENTION_BOUND_FIELDS {
                let Some(candidates) = mentions_by_field.get(field) else {
                    continue;
                };
                if event_structured_field_present(event, field) {
                    continue;
                }

                mismatch_detected += 1;
                mismatch_classes.insert(AUDIT_CLASS_MISSING_MENTION_FIELD.to_string(), ());
                if candidates.len() == 1 && auto_repair_allowed {
                    let value = candidates.values().next().cloned().unwrap_or(Value::Null);
                    repair_fields_by_target
                        .entry(receipt.event_id)
                        .or_default()
                        .insert(field.to_string(), value);
                    session_id_by_target
                        .entry(receipt.event_id)
                        .or_insert_with(|| event.metadata.session_id.clone());
                    mismatch_repaired += 1;
                    continue;
                }

                mismatch_unresolved += 1;
                unresolved.push(SessionAuditUnresolved {
                    exercise_label: exercise_label_for_event(event),
                    field: field.to_string(),
                    candidates: candidates.keys().cloned().collect(),
                });
            }

            continue;
        }

        if event_type != "session.completed" {
            continue;
        }

        let mut normalized_updates: BTreeMap<String, Value> = BTreeMap::new();
        for (field, max_scale, allow_ten_to_five) in [
            ("enjoyment", 5.0_f64, true),
            ("perceived_quality", 5.0_f64, true),
            ("perceived_exertion", 10.0_f64, false),
        ] {
            if let Some(raw) = extract_feedback_scale_value(event, field) {
                if allow_ten_to_five && raw > 5.0 && raw <= 10.0 {
                    mismatch_detected += 1;
                    mismatch_classes.insert(AUDIT_CLASS_SCALE_NORMALIZED_TO_FIVE.to_string(), ());
                    let normalized = round_to_two(raw / 2.0);
                    if auto_repair_allowed {
                        normalized_updates.insert(field.to_string(), json!(normalized));
                        mismatch_repaired += 1;
                    } else {
                        mismatch_unresolved += 1;
                        unresolved.push(SessionAuditUnresolved {
                            exercise_label: "Session-Feedback".to_string(),
                            field: field.to_string(),
                            candidates: vec![format!("{normalized:.2}")],
                        });
                    }
                } else if raw < 1.0 || raw > max_scale {
                    mismatch_detected += 1;
                    mismatch_unresolved += 1;
                    mismatch_classes.insert(AUDIT_CLASS_SCALE_OUT_OF_BOUNDS.to_string(), ());
                    unresolved.push(SessionAuditUnresolved {
                        exercise_label: "Session-Feedback".to_string(),
                        field: field.to_string(),
                        candidates: vec![format!("{raw:.2}")],
                    });
                }
            }

            if has_unsupported_inferred_value(event, field) {
                mismatch_detected += 1;
                mismatch_unresolved += 1;
                mismatch_classes.insert(AUDIT_CLASS_UNSUPPORTED_INFERRED.to_string(), ());
                let inferred_value = event
                    .data
                    .get(field)
                    .map(canonical_mention_value)
                    .unwrap_or_else(|| "inferred".to_string());
                unresolved.push(SessionAuditUnresolved {
                    exercise_label: "Session-Feedback".to_string(),
                    field: field.to_string(),
                    candidates: vec![inferred_value],
                });
            }
        }

        if let Some(context) = extract_session_feedback_context(event) {
            let has_positive = contains_any_hint(&context, &SESSION_POSITIVE_HINTS);
            let has_negative = contains_any_hint(&context, &SESSION_NEGATIVE_HINTS);
            let has_easy = contains_any_hint(&context, &SESSION_EASY_HINTS);
            let has_hard = contains_any_hint(&context, &SESSION_HARD_HINTS);

            for field in ["enjoyment", "perceived_quality"] {
                if let Some(value) = extract_feedback_scale_value(event, field) {
                    let contradicts =
                        (value >= 4.0 && has_negative) || (value <= 2.5 && has_positive);
                    if contradicts {
                        mismatch_detected += 1;
                        mismatch_unresolved += 1;
                        mismatch_classes
                            .insert(AUDIT_CLASS_NARRATIVE_CONTRADICTION.to_string(), ());
                        unresolved.push(SessionAuditUnresolved {
                            exercise_label: "Session-Feedback".to_string(),
                            field: field.to_string(),
                            candidates: vec![format!("{value:.2}")],
                        });
                    }
                }
            }

            if let Some(exertion) = extract_feedback_scale_value(event, "perceived_exertion") {
                let contradicts = (exertion >= 8.0 && has_easy) || (exertion <= 4.0 && has_hard);
                if contradicts {
                    mismatch_detected += 1;
                    mismatch_unresolved += 1;
                    mismatch_classes.insert(AUDIT_CLASS_NARRATIVE_CONTRADICTION.to_string(), ());
                    unresolved.push(SessionAuditUnresolved {
                        exercise_label: "Session-Feedback".to_string(),
                        field: "perceived_exertion".to_string(),
                        candidates: vec![format!("{exertion:.2}")],
                    });
                }
            }
        }

        if auto_repair_allowed && !normalized_updates.is_empty() {
            let mut normalized_seed = normalized_updates
                .iter()
                .map(|(field, value)| format!("{field}:{}", canonical_mention_value(value)))
                .collect::<Vec<_>>();
            normalized_seed.sort();
            let seed = format!(
                "session_feedback_audit|{}|{}",
                receipt.event_id,
                normalized_seed.join("|")
            );
            let retract_key = format!("session-audit-retract-{}", stable_hash_suffix(&seed, 20));
            let replace_key = format!(
                "session-audit-replacement-{}",
                stable_hash_suffix(&(seed.clone() + "|replace"), 20)
            );
            let session_id = event
                .metadata
                .session_id
                .clone()
                .or_else(|| Some("session_audit".to_string()));

            session_feedback_repair_events.push(CreateEventRequest {
                timestamp: Utc::now(),
                event_type: "event.retracted".to_string(),
                data: serde_json::json!({
                    "retracted_event_id": receipt.event_id,
                    "retracted_event_type": "session.completed",
                    "reason": "Session audit deterministic scale normalization."
                }),
                metadata: EventMetadata {
                    source: Some("agent_write_with_proof".to_string()),
                    agent: Some("api".to_string()),
                    device: None,
                    session_id: session_id.clone(),
                    idempotency_key: retract_key,
                },
            });

            let mut replacement_payload = event
                .data
                .as_object()
                .cloned()
                .unwrap_or_else(serde_json::Map::new);
            for (field, value) in normalized_updates {
                replacement_payload.insert(field, value);
            }
            replacement_payload.insert(
                "repair_provenance".to_string(),
                serde_json::json!({
                    "source_type": "inferred",
                    "confidence": 0.98,
                    "confidence_band": "high",
                    "applies_scope": "session",
                    "reason": "Session audit deterministic scale normalization (1..10 -> 1..5)."
                }),
            );
            replacement_payload.insert(
                "audit_repair_of_event_id".to_string(),
                serde_json::json!(receipt.event_id),
            );

            session_feedback_repair_events.push(CreateEventRequest {
                timestamp: Utc::now(),
                event_type: "session.completed".to_string(),
                data: Value::Object(replacement_payload),
                metadata: EventMetadata {
                    source: Some("agent_write_with_proof".to_string()),
                    agent: Some("api".to_string()),
                    device: None,
                    session_id,
                    idempotency_key: replace_key,
                },
            });
        }
    }

    let mut repair_events = Vec::new();
    for (target_event_id, changed_fields) in repair_fields_by_target {
        if changed_fields.is_empty() {
            continue;
        }

        let mut changed_fields_payload = serde_json::Map::new();
        let mut seed_parts = Vec::new();
        for (field, value) in changed_fields {
            seed_parts.push(format!("{field}:{}", canonical_mention_value(&value)));
            changed_fields_payload.insert(
                field.clone(),
                serde_json::json!({
                    "value": value,
                    "repair_provenance": {
                        "source_type": "inferred",
                        "confidence": 0.95,
                        "confidence_band": "high",
                        "applies_scope": "single_set",
                        "reason": "Deterministic mention-field session audit."
                    }
                }),
            );
        }
        seed_parts.sort();
        let seed = format!("session_audit|{}|{}", target_event_id, seed_parts.join("|"));
        let idempotency_key = format!("session-audit-correction-{}", stable_hash_suffix(&seed, 20));
        let session_id = session_id_by_target
            .get(&target_event_id)
            .cloned()
            .flatten()
            .or_else(|| Some("session_audit".to_string()));

        repair_events.push(CreateEventRequest {
            timestamp: Utc::now(),
            event_type: "set.corrected".to_string(),
            data: serde_json::json!({
                "target_event_id": target_event_id,
                "changed_fields": changed_fields_payload,
                "reason": "Session audit auto-repair persisted mention-bound fields.",
                "repair_provenance": {
                    "source_type": "inferred",
                    "confidence": 0.95,
                    "confidence_band": "high",
                    "applies_scope": "single_set",
                    "reason": "Deterministic mention-field session audit."
                },
            }),
            metadata: EventMetadata {
                source: Some("agent_write_with_proof".to_string()),
                agent: Some("api".to_string()),
                device: None,
                session_id,
                idempotency_key,
            },
        });
    }
    repair_events.extend(session_feedback_repair_events);

    let status = if mismatch_detected == 0 {
        "clean".to_string()
    } else if mismatch_unresolved == 0 && mismatch_repaired > 0 {
        "repaired".to_string()
    } else {
        "needs_clarification".to_string()
    };
    let clarification_question = if status == "needs_clarification" {
        build_clarification_question(&unresolved)
    } else {
        None
    };
    let summary = AgentSessionAuditSummary {
        status,
        mismatch_detected,
        mismatch_repaired,
        mismatch_unresolved,
        mismatch_classes: mismatch_classes.into_keys().collect(),
        clarification_question,
    };

    let mut telemetry_events = Vec::new();
    if summary.mismatch_detected > 0 {
        telemetry_events.push(build_session_audit_learning_signal_event(
            user_id,
            "mismatch_detected",
            &summary,
        ));
    }
    if summary.mismatch_repaired > 0 {
        telemetry_events.push(build_session_audit_learning_signal_event(
            user_id,
            "mismatch_repaired",
            &summary,
        ));
    }
    if summary.mismatch_unresolved > 0 {
        telemetry_events.push(build_session_audit_learning_signal_event(
            user_id,
            "mismatch_unresolved",
            &summary,
        ));
    }

    SessionAuditArtifacts {
        summary,
        repair_events,
        telemetry_events,
    }
}

fn build_repair_feedback_summary(summary: &AgentSessionAuditSummary) -> String {
    match summary.status.as_str() {
        "clean" => {
            "Keine Reparatur nötig. Mention-gebundene Felder und Session-Feedback sind konsistent gespeichert."
                .to_string()
        }
        "repaired" => format!(
            "Ich habe {} Audit-Mismatches automatisch repariert. Bestehende Daten bleiben nachvollziehbar korrigierbar.",
            summary.mismatch_repaired
        ),
        "needs_clarification" if summary.mismatch_repaired > 0 => format!(
            "Ich habe {} Felder ergänzt. Für {} Punkt(e) brauche ich noch eine kurze Rückmeldung.",
            summary.mismatch_repaired, summary.mismatch_unresolved
        ),
        _ => "Ich brauche eine kurze Rückfrage, bevor ich fehlende Felder sicher speichern kann."
            .to_string(),
    }
}

fn build_undo_event_templates(
    repair_receipts: &[AgentWriteReceipt],
) -> Vec<AgentUndoEventTemplate> {
    let mut events = Vec::with_capacity(repair_receipts.len());
    for receipt in repair_receipts {
        let seed = format!("session_audit_undo|{}", receipt.event_id);
        let idempotency_key = format!("session-audit-undo-{}", stable_hash_suffix(&seed, 20));
        events.push(AgentUndoEventTemplate {
            timestamp: Utc::now(),
            event_type: "event.retracted".to_string(),
            data: serde_json::json!({
                "retracted_event_id": receipt.event_id,
                "retracted_event_type": receipt.event_type,
                "reason": "Undo session-audit auto-repair batch."
            }),
            metadata: EventMetadata {
                source: Some("agent_write_with_proof".to_string()),
                agent: Some("api".to_string()),
                device: None,
                session_id: Some("session_audit:undo".to_string()),
                idempotency_key,
            },
        });
    }
    events
}

fn build_repair_technical_details(
    repair_events: &[CreateEventRequest],
    repair_receipts: &[AgentWriteReceipt],
) -> AgentRepairTechnicalDetails {
    let mut target_event_ids: BTreeMap<String, ()> = BTreeMap::new();
    let mut field_diffs = Vec::new();

    for event in repair_events {
        let target_event_id = event
            .data
            .get("target_event_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if !target_event_id.is_empty() {
            target_event_ids.insert(target_event_id.clone(), ());
        }

        if let Some(changed_fields) = event.data.get("changed_fields").and_then(Value::as_object) {
            for (field, raw) in changed_fields {
                let value = raw.get("value").cloned().unwrap_or(Value::Null);
                field_diffs.push(AgentRepairFieldDiff {
                    target_event_id: target_event_id.clone(),
                    field: field.clone(),
                    value,
                });
            }
        }
    }

    AgentRepairTechnicalDetails {
        repair_event_ids: repair_receipts
            .iter()
            .map(|receipt| receipt.event_id)
            .collect(),
        target_event_ids: target_event_ids.into_keys().collect(),
        field_diffs,
        command_trace: vec![
            "session_audit.scan_mentions".to_string(),
            "session_audit.apply_set_corrected".to_string(),
            "session_audit.apply_session_feedback_scale_repair".to_string(),
            "session_audit.prepare_undo".to_string(),
        ],
    }
}

fn build_repair_feedback(
    include_technical_details: bool,
    session_audit_summary: &AgentSessionAuditSummary,
    repair_events: &[CreateEventRequest],
    repair_receipts: &[AgentWriteReceipt],
    requested_event_count: usize,
    verification: &AgentWriteVerificationSummary,
    claim_guard: &AgentWriteClaimGuard,
) -> AgentRepairFeedback {
    let status = if session_audit_summary.status == "clean" {
        "none".to_string()
    } else {
        session_audit_summary.status.clone()
    };
    let mut unchanged_metrics = HashMap::new();
    unchanged_metrics.insert(
        "requested_event_count".to_string(),
        serde_json::json!(requested_event_count),
    );
    unchanged_metrics.insert(
        "required_checks".to_string(),
        serde_json::json!(verification.required_checks),
    );
    unchanged_metrics.insert(
        "verified_checks".to_string(),
        serde_json::json!(verification.verified_checks),
    );
    unchanged_metrics.insert(
        "verification_status".to_string(),
        serde_json::json!(verification.status),
    );
    unchanged_metrics.insert(
        "claim_status".to_string(),
        serde_json::json!(claim_guard.claim_status),
    );

    let undo_events = build_undo_event_templates(repair_receipts);
    let undo = if undo_events.is_empty() {
        None
    } else {
        Some(AgentRepairUndoAction {
            available: true,
            detail: "Undo verfügbar: sende `undo.events` als Batch an `/v1/events/batch`."
                .to_string(),
            events: undo_events,
        })
    };

    let technical = if include_technical_details {
        Some(build_repair_technical_details(
            repair_events,
            repair_receipts,
        ))
    } else {
        None
    };

    AgentRepairFeedback {
        status: status.clone(),
        summary: build_repair_feedback_summary(session_audit_summary),
        receipt: AgentRepairReceipt {
            status,
            changed_fields_count: session_audit_summary.mismatch_repaired,
            unchanged_metrics,
        },
        clarification_question: session_audit_summary.clarification_question.clone(),
        undo,
        technical,
    }
}

async fn evaluate_read_after_write_checks(
    state: &AppState,
    user_id: Uuid,
    targets: &[(String, String)],
    event_ids: &HashSet<Uuid>,
) -> Result<Vec<AgentReadAfterWriteCheck>, AppError> {
    let mut tx = state.db.begin().await?;

    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let mut checks = Vec::with_capacity(targets.len());
    for (projection_type, key) in targets {
        let projection = fetch_projection(&mut tx, user_id, projection_type, key).await?;
        match projection {
            Some(response) => {
                let observed_last_event_id = response.projection.last_event_id;
                let verified = observed_last_event_id
                    .map(|id| event_ids.contains(&id))
                    .unwrap_or(false);

                let detail = if verified {
                    "Projection read-after-write verified via matching last_event_id.".to_string()
                } else if observed_last_event_id.is_some() {
                    "Projection found but last_event_id does not match current write receipts yet."
                        .to_string()
                } else {
                    "Projection found but has no last_event_id; cannot verify this write yet."
                        .to_string()
                };

                checks.push(AgentReadAfterWriteCheck {
                    projection_type: projection_type.clone(),
                    key: key.clone(),
                    status: if verified {
                        "verified".to_string()
                    } else {
                        "pending".to_string()
                    },
                    observed_projection_version: Some(response.projection.version),
                    observed_last_event_id,
                    detail,
                });
            }
            None => checks.push(AgentReadAfterWriteCheck {
                projection_type: projection_type.clone(),
                key: key.clone(),
                status: "pending".to_string(),
                observed_projection_version: None,
                observed_last_event_id: None,
                detail: "Projection row not found yet for this target.".to_string(),
            }),
        }
    }

    tx.commit().await?;
    Ok(checks)
}

async fn verify_read_after_write_until_timeout(
    state: &AppState,
    user_id: Uuid,
    targets: &[(String, String)],
    event_ids: &HashSet<Uuid>,
    verify_timeout_ms: u64,
) -> Result<(Vec<AgentReadAfterWriteCheck>, u64), AppError> {
    let started = Instant::now();
    let timeout = Duration::from_millis(verify_timeout_ms);
    let poll_interval = Duration::from_millis(100);

    let mut checks = evaluate_read_after_write_checks(state, user_id, targets, event_ids).await?;
    while !all_read_after_write_verified(&checks) && started.elapsed() < timeout {
        tokio::time::sleep(poll_interval).await;
        checks = evaluate_read_after_write_checks(state, user_id, targets, event_ids).await?;
    }

    let waited_ms = started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
    Ok((checks, waited_ms))
}

/// Resolve whether a visualization should be shown and validate data-bound specs.
///
/// Decision 13 + pdc.6 semantics:
/// - policy-driven visualization gating based on intent, complexity, and user override
/// - structured visualization_spec required before rendering when policy says "visualize"
/// - strict source binding to resolvable projection references
/// - deterministic ASCII fallback when rich rendering is unavailable
#[utoipa::path(
    post,
    path = "/v1/agent/visualization/resolve",
    request_body = AgentResolveVisualizationRequest,
    responses(
        (status = 200, description = "Visualization policy decision + resolved output", body = AgentResolveVisualizationResponse),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn resolve_visualization(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<AgentResolveVisualizationRequest>,
) -> Result<Json<AgentResolveVisualizationResponse>, AppError> {
    require_scopes(
        &auth,
        &["agent:resolve"],
        "POST /v1/agent/visualization/resolve",
    )?;
    let user_id = auth.user_id;
    let task_intent = req.task_intent.trim();
    if task_intent.is_empty() {
        return Err(AppError::Validation {
            message: "task_intent must not be empty".to_string(),
            field: Some("task_intent".to_string()),
            received: None,
            docs_hint: Some(
                "Provide a concrete intent, e.g. 'compare last 4 weeks volume vs plan'."
                    .to_string(),
            ),
        });
    }

    let source_count_hint = req
        .visualization_spec
        .as_ref()
        .map(|spec| spec.data_sources.len())
        .unwrap_or(0);
    let mut policy = visualization_policy_decision(
        task_intent,
        req.user_preference_override.as_deref(),
        req.complexity_hint.as_deref(),
        source_count_hint,
    );
    let user_profile = fetch_user_profile_projection(&state, user_id).await?;
    let quality_health = fetch_quality_health_projection(&state, user_id).await?;
    let skip_uncertainty = visualization_uncertainty_label(quality_health.as_ref());

    if policy.status == "skipped" {
        let telemetry_events = build_visualization_learning_signal_events(
            user_id,
            &policy,
            None,
            &[],
            None,
            skip_uncertainty.as_deref(),
            req.telemetry_session_id.as_deref(),
        );
        let telemetry_signal_types: Vec<String> = telemetry_events
            .iter()
            .filter_map(|event| {
                event
                    .data
                    .get("signal_type")
                    .and_then(Value::as_str)
                    .map(|value| value.to_string())
            })
            .collect();
        let _ = create_events_batch_internal(&state, user_id, &telemetry_events).await;

        return Ok(Json(AgentResolveVisualizationResponse {
            policy,
            visualization_spec: None,
            resolved_sources: Vec::new(),
            timezone_context: None,
            uncertainty_label: skip_uncertainty,
            output: AgentVisualizationOutput {
                format: "text".to_string(),
                content:
                    "Visualization skipped by policy. Provide explicit compare/trend/plan-vs-actual/multi-week intent or user override if a visual is needed."
                        .to_string(),
            },
            fallback_output: None,
            warnings: Vec::new(),
            telemetry_signal_types,
        }));
    }

    let normalized_spec =
        normalize_visualization_spec(req.visualization_spec.ok_or_else(|| {
            AppError::Validation {
            message: "visualization_spec is required when policy decides visualization".to_string(),
            field: Some("visualization_spec".to_string()),
            received: None,
            docs_hint: Some(
                "Send visualization_spec with format, purpose, and data_sources before rendering."
                    .to_string(),
            ),
        }
        })?)?;
    let resolved_sources = resolve_visualization_sources(&state, user_id, &normalized_spec).await?;
    let timezone_context =
        resolve_visualization_timezone_context(&normalized_spec, user_profile.as_ref());
    let uncertainty_label = visualization_uncertainty_label(quality_health.as_ref());

    let (resolved_status, output, fallback_output, warnings) = build_visualization_outputs(
        &normalized_spec,
        &resolved_sources,
        &timezone_context,
        req.allow_rich_rendering,
        uncertainty_label.as_deref(),
    );
    policy.status = resolved_status;
    if policy.status == "fallback" {
        policy.reason =
            "Rich rendering unavailable; deterministic ASCII fallback returned.".to_string();
    }

    let telemetry_events = build_visualization_learning_signal_events(
        user_id,
        &policy,
        Some(&normalized_spec),
        &resolved_sources,
        Some(&timezone_context),
        uncertainty_label.as_deref(),
        req.telemetry_session_id.as_deref(),
    );
    let telemetry_signal_types: Vec<String> = telemetry_events
        .iter()
        .filter_map(|event| {
            event
                .data
                .get("signal_type")
                .and_then(Value::as_str)
                .map(|value| value.to_string())
        })
        .collect();
    let _ = create_events_batch_internal(&state, user_id, &telemetry_events).await;

    Ok(Json(AgentResolveVisualizationResponse {
        policy,
        visualization_spec: Some(normalized_spec),
        resolved_sources,
        timezone_context: Some(timezone_context),
        uncertainty_label,
        output,
        fallback_output,
        warnings,
        telemetry_signal_types,
    }))
}

/// Explain lineage claims for a persisted event.
#[utoipa::path(
    get,
    path = "/v1/agent/evidence/event/{event_id}",
    params(
        ("event_id" = Uuid, Path, description = "Target event ID to inspect evidence claims for")
    ),
    responses(
        (status = 200, description = "Evidence lineage for the target event", body = AgentEventEvidenceResponse),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_event_evidence_lineage(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path(event_id): Path<Uuid>,
) -> Result<Json<AgentEventEvidenceResponse>, AppError> {
    require_scopes(
        &auth,
        &["agent:read"],
        "GET /v1/agent/evidence/event/{event_id}",
    )?;
    let user_id = auth.user_id;
    let mut tx = state.db.begin().await?;

    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let rows = sqlx::query_as::<_, EvidenceClaimEventRow>(
        r#"
        SELECT id, timestamp, data
        FROM events
        WHERE user_id = $1
          AND event_type = 'evidence.claim.logged'
          AND data->'lineage'->>'event_id' = $2
        ORDER BY timestamp ASC, id ASC
        LIMIT 512
        "#,
    )
    .bind(user_id)
    .bind(event_id.to_string())
    .fetch_all(&mut *tx)
    .await?;

    tx.commit().await?;

    let claims = rows
        .into_iter()
        .map(|row| AgentEvidenceClaim {
            claim_event_id: row.id,
            claim_id: row
                .data
                .get("claim_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            claim_type: row
                .data
                .get("claim_type")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            value: row.data.get("value").cloned().unwrap_or(Value::Null),
            unit: row
                .data
                .get("unit")
                .and_then(Value::as_str)
                .map(str::to_string),
            scope: row.data.get("scope").cloned().unwrap_or(Value::Null),
            confidence: row
                .data
                .get("confidence")
                .and_then(Value::as_f64)
                .unwrap_or(0.0),
            provenance: row.data.get("provenance").cloned().unwrap_or(Value::Null),
            lineage: row.data.get("lineage").cloned().unwrap_or(Value::Null),
            recorded_at: row.timestamp,
        })
        .collect();

    Ok(Json(AgentEventEvidenceResponse { event_id, claims }))
}

/// Write events with durable receipts and read-after-write verification.
///
/// This endpoint enforces Decision 13.5 protocol semantics:
/// - write-with-proof (event ids + idempotency keys)
/// - read-after-write check against required projection targets
/// - explicit deferred/uncertainty markers when proof is incomplete
#[utoipa::path(
    post,
    path = "/v1/agent/write-with-proof",
    request_body = AgentWriteWithProofRequest,
    responses(
        (status = 201, description = "Events written with verification result", body = AgentWriteWithProofResponse),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 409, description = "Idempotency conflict", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn write_with_proof(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<AgentWriteWithProofRequest>,
) -> Result<impl axum::response::IntoResponse, AppError> {
    require_scopes(&auth, &["agent:write"], "POST /v1/agent/write-with-proof")?;
    let user_id = auth.user_id;
    let requested_event_count = req.events.len();
    let verify_timeout_ms = clamp_verify_timeout_ms(req.verify_timeout_ms);
    let read_after_write_targets = normalize_read_after_write_targets(req.read_after_write_targets);

    if read_after_write_targets.is_empty() {
        return Err(AppError::Validation {
            message: "read_after_write_targets must not be empty".to_string(),
            field: Some("read_after_write_targets".to_string()),
            received: None,
            docs_hint: Some(
                "Provide at least one projection_type/key target for read-after-write verification."
                    .to_string(),
            ),
        });
    }
    validate_session_feedback_certainty_contract(&req.events)?;

    let user_profile = fetch_user_profile_projection(&state, user_id).await?;
    let workflow_state = fetch_workflow_state(&state, user_id, user_profile.as_ref()).await?;
    let workflow_gate = workflow_gate_from_request(&req.events, &workflow_state);
    let requested_close = req.events.iter().any(|event| {
        event
            .event_type
            .trim()
            .eq_ignore_ascii_case(WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE)
    });
    if workflow_gate.status == "blocked" {
        if let Some(signal) = build_workflow_gate_learning_signal_event(user_id, &workflow_gate) {
            let _ = create_events_batch_internal(&state, user_id, &[signal]).await;
        }
        let docs_hint = format!(
            "Planning/coaching events require onboarding close ({WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE}) or explicit override ({WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE}). Missing requirements: {}",
            workflow_gate.missing_requirements.join(", ")
        );
        return Err(AppError::Validation {
            message: workflow_gate.message.clone(),
            field: Some("events".to_string()),
            received: Some(serde_json::json!({
                "planning_event_types": workflow_gate.planning_event_types,
                "missing_requirements": workflow_gate.missing_requirements,
                "phase": workflow_gate.phase,
            })),
            docs_hint: Some(docs_hint),
        });
    }

    let mut auto_close_applied = false;
    if workflow_gate.transition == "onboarding_closed"
        && !requested_close
        && !workflow_state.onboarding_closed
    {
        let auto_close_event = build_auto_onboarding_close_event(&req.events);
        let (_auto_close_receipts, _auto_close_warnings, _auto_close_write_path) =
            write_events_with_receipts(
                &state,
                user_id,
                &[auto_close_event],
                "workflow_auto_close.idempotency_key",
            )
            .await?;
        auto_close_applied = true;
    }

    let mut workflow_warnings: Vec<BatchEventWarning> = Vec::new();
    if workflow_gate.transition == "onboarding_closed" {
        workflow_warnings.push(BatchEventWarning {
            event_index: 0,
            field: "workflow.phase".to_string(),
            message: if auto_close_applied {
                "Legacy compatibility: onboarding close marker auto-recorded; planning/coaching phase is active."
                    .to_string()
            } else {
                "Onboarding close transition accepted. Planning/coaching phase is active."
                    .to_string()
            },
            severity: "info".to_string(),
        });
    } else if workflow_gate.transition == "override" {
        workflow_warnings.push(BatchEventWarning {
            event_index: 0,
            field: "workflow.phase".to_string(),
            message: "Planning/coaching phase allowed via explicit onboarding override."
                .to_string(),
            severity: "warning".to_string(),
        });
    }

    let (receipts, mut warnings, write_path) =
        write_events_with_receipts(&state, user_id, &req.events, "metadata.idempotency_key")
            .await?;
    warnings.extend(workflow_warnings);
    let quality_health = fetch_quality_health_projection(&state, user_id).await?;
    let autonomy_policy = autonomy_policy_from_quality_health(quality_health.as_ref());
    let SessionAuditArtifacts {
        summary: session_audit_summary,
        repair_events,
        telemetry_events,
    } = build_session_audit_artifacts(user_id, &req.events, &receipts, &autonomy_policy);

    let mut event_ids: HashSet<Uuid> = receipts.iter().map(|receipt| receipt.event_id).collect();
    let mut repair_receipts: Vec<AgentWriteReceipt> = Vec::new();
    if !repair_events.is_empty() {
        let (written_repair_receipts, repair_warnings, _repair_write_path) =
            write_events_with_receipts(
                &state,
                user_id,
                &repair_events,
                "session_audit.idempotency_key",
            )
            .await?;
        warnings.extend(repair_warnings);
        event_ids.extend(
            written_repair_receipts
                .iter()
                .map(|receipt| receipt.event_id),
        );
        repair_receipts = written_repair_receipts;
    }

    let (checks, waited_ms) = verify_read_after_write_until_timeout(
        &state,
        user_id,
        &read_after_write_targets,
        &event_ids,
        verify_timeout_ms,
    )
    .await?;

    let verified_checks = checks
        .iter()
        .filter(|check| check.status == "verified")
        .count();
    let receipts_complete = receipts.len() == requested_event_count
        && receipts
            .iter()
            .all(|receipt| !receipt.idempotency_key.trim().is_empty());
    let verification_status = if !receipts_complete {
        "failed".to_string()
    } else if verified_checks == checks.len() {
        "verified".to_string()
    } else {
        "pending".to_string()
    };

    let verification = AgentWriteVerificationSummary {
        status: verification_status,
        checked_at: Utc::now(),
        waited_ms,
        write_path,
        required_checks: checks.len(),
        verified_checks,
        checks,
    };
    let claim_guard = build_claim_guard(
        &receipts,
        requested_event_count,
        &verification.checks,
        &warnings,
        autonomy_policy,
    );
    let quality_signal = build_save_claim_checked_event(
        requested_event_count,
        &receipts,
        &verification,
        &claim_guard,
        &session_audit_summary,
    );
    let mut quality_events = vec![quality_signal];
    quality_events.extend(build_save_handshake_learning_signal_events(
        user_id,
        requested_event_count,
        &receipts,
        &verification,
        &claim_guard,
    ));
    if let Some(workflow_signal) =
        build_workflow_gate_learning_signal_event(user_id, &workflow_gate)
    {
        quality_events.push(workflow_signal);
    }
    quality_events.extend(telemetry_events);
    let _ = create_events_batch_internal(&state, user_id, &quality_events).await;
    let evidence_events = build_evidence_claim_events(user_id, &req.events, &receipts);
    if !evidence_events.is_empty() {
        let _ = create_events_batch_internal(&state, user_id, &evidence_events).await;
    }
    let inferred_facts = collect_reliability_inferred_facts(&evidence_events, &repair_events);
    let reliability_ux = build_reliability_ux(&claim_guard, &session_audit_summary, inferred_facts);
    let repair_feedback = build_repair_feedback(
        req.include_repair_technical_details,
        &session_audit_summary,
        &repair_events,
        &repair_receipts,
        requested_event_count,
        &verification,
        &claim_guard,
    );

    Ok((
        StatusCode::CREATED,
        Json(AgentWriteWithProofResponse {
            receipts,
            warnings,
            verification,
            claim_guard,
            reliability_ux,
            workflow_gate,
            session_audit: session_audit_summary,
            repair_feedback,
        }),
    ))
}

/// Get machine-readable capability manifest for agent contract negotiation.
#[utoipa::path(
    get,
    path = "/v1/agent/capabilities",
    responses(
        (status = 200, description = "Agent capability manifest", body = AgentCapabilitiesResponse),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_agent_capabilities(
    auth: AuthenticatedUser,
) -> Result<Json<AgentCapabilitiesResponse>, AppError> {
    require_scopes(&auth, &["agent:read"], "GET /v1/agent/capabilities")?;
    Ok(Json(build_agent_capabilities()))
}

/// Get agent context bundle in a single read call.
///
/// Returns the deployment-static system config, user profile, and key
/// projections that agents typically need to answer user requests.
#[utoipa::path(
    get,
    path = "/v1/agent/context",
    params(AgentContextParams),
    responses(
        (status = 200, description = "Agent context bundle", body = AgentContextResponse),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_agent_context(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Query(params): Query<AgentContextParams>,
) -> Result<Json<AgentContextResponse>, AppError> {
    require_scopes(&auth, &["agent:read"], "GET /v1/agent/context")?;
    let user_id = auth.user_id;
    let exercise_limit = clamp_limit(params.exercise_limit, 5, 100);
    let strength_limit = clamp_limit(params.strength_limit, 5, 100);
    let custom_limit = clamp_limit(params.custom_limit, 10, 100);
    let task_intent = params.task_intent.and_then(|raw| {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    });

    let mut tx = state.db.begin().await?;

    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let system = sqlx::query_as::<_, SystemConfigRow>(
        "SELECT data, version, updated_at FROM system_config WHERE key = 'global'",
    )
    .fetch_optional(&mut *tx)
    .await?
    .map(|row| {
        redact_system_config_for_agent(SystemConfigResponse {
            data: row.data,
            version: row.version,
            updated_at: row.updated_at,
        })
    });

    let user_profile = fetch_projection(&mut tx, user_id, "user_profile", "me")
        .await?
        .unwrap_or_else(|| bootstrap_user_profile(user_id));

    let training_timeline =
        fetch_projection(&mut tx, user_id, "training_timeline", "overview").await?;
    let session_feedback =
        fetch_projection(&mut tx, user_id, "session_feedback", "overview").await?;
    let body_composition =
        fetch_projection(&mut tx, user_id, "body_composition", "overview").await?;
    let recovery = fetch_projection(&mut tx, user_id, "recovery", "overview").await?;
    let nutrition = fetch_projection(&mut tx, user_id, "nutrition", "overview").await?;
    let training_plan = fetch_projection(&mut tx, user_id, "training_plan", "overview").await?;
    let semantic_memory = fetch_projection(&mut tx, user_id, "semantic_memory", "overview").await?;
    let readiness_inference =
        fetch_projection(&mut tx, user_id, "readiness_inference", "overview").await?;
    let causal_inference =
        fetch_projection(&mut tx, user_id, "causal_inference", "overview").await?;
    let quality_health = fetch_projection(&mut tx, user_id, "quality_health", "overview").await?;

    let ranking_context =
        RankingContext::from_task_intent(task_intent.clone(), semantic_memory.as_ref());

    let exercise_candidates = fetch_projection_list(
        &mut tx,
        user_id,
        "exercise_progression",
        ranking_candidate_limit(exercise_limit),
    )
    .await?;
    let strength_candidates = fetch_projection_list(
        &mut tx,
        user_id,
        "strength_inference",
        ranking_candidate_limit(strength_limit),
    )
    .await?;
    let custom_candidates = fetch_projection_list(
        &mut tx,
        user_id,
        "custom",
        ranking_candidate_limit(custom_limit),
    )
    .await?;

    tx.commit().await?;

    let exercise_progression =
        rank_projection_list(exercise_candidates, exercise_limit, &ranking_context);
    let strength_inference =
        rank_projection_list(strength_candidates, strength_limit, &ranking_context);
    let custom = rank_projection_list(custom_candidates, custom_limit, &ranking_context);

    Ok(Json(AgentContextResponse {
        system,
        user_profile,
        training_timeline,
        session_feedback,
        body_composition,
        recovery,
        nutrition,
        training_plan,
        semantic_memory,
        readiness_inference,
        causal_inference,
        quality_health,
        exercise_progression,
        strength_inference,
        custom,
        meta: AgentContextMeta {
            generated_at: Utc::now(),
            exercise_limit,
            strength_limit,
            custom_limit,
            task_intent: ranking_context.intent.clone(),
            ranking_strategy: "composite(recency,confidence,semantic_relevance,task_intent)"
                .to_string(),
            context_contract_version: AGENT_CONTEXT_CONTRACT_VERSION.to_string(),
            system_contract: build_agent_context_system_contract(),
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::{
        AgentReadAfterWriteCheck, AgentReadAfterWriteTarget, AgentResolveVisualizationRequest,
        AgentVisualizationDataSource, AgentVisualizationResolvedSource, AgentVisualizationSpec,
        AgentVisualizationTimezoneContext, AgentWorkflowState, AgentWriteReceipt, IntentClass,
        ProjectionResponse, RankingContext, WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE,
        WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE, bind_visualization_source, bootstrap_user_profile,
        build_agent_capabilities, build_auto_onboarding_close_event, build_claim_guard,
        build_evidence_claim_events, build_reliability_ux, build_repair_feedback,
        build_save_handshake_learning_signal_events, build_session_audit_artifacts,
        build_visualization_outputs, clamp_limit, clamp_verify_timeout_ms,
        collect_reliability_inferred_facts, default_autonomy_policy, extract_evidence_claim_drafts,
        extract_set_context_mentions_from_text, missing_onboarding_close_requirements,
        normalize_read_after_write_targets, normalize_set_type, normalize_visualization_spec,
        parse_rest_seconds_from_text, parse_rest_with_span, parse_rir_from_text,
        parse_rir_with_span, parse_set_type_with_span, parse_tempo_from_text,
        parse_tempo_with_span, rank_projection_list, ranking_candidate_limit,
        recover_receipts_for_idempotent_retry, resolve_visualization,
        validate_session_feedback_certainty_contract, visualization_policy_decision,
        workflow_gate_from_request,
    };
    use crate::auth::{AuthMethod, AuthenticatedUser};
    use crate::error::AppError;
    use crate::state::AppState;
    use axum::{
        Json,
        extract::{Path, State},
    };
    use chrono::{Duration, Utc};
    use kura_core::events::{BatchEventWarning, CreateEventRequest, EventMetadata};
    use kura_core::projections::{Projection, ProjectionFreshness, ProjectionMeta};
    use serde_json::{Value, json};
    use sqlx::postgres::PgPoolOptions;
    use uuid::Uuid;

    fn make_projection_response(
        projection_type: &str,
        key: &str,
        updated_at: chrono::DateTime<Utc>,
        data: serde_json::Value,
    ) -> ProjectionResponse {
        let now = Utc::now();
        ProjectionResponse {
            projection: Projection {
                id: Uuid::now_v7(),
                user_id: Uuid::now_v7(),
                projection_type: projection_type.to_string(),
                key: key.to_string(),
                data,
                version: 1,
                last_event_id: None,
                updated_at,
            },
            meta: ProjectionMeta {
                projection_version: 1,
                computed_at: updated_at,
                freshness: ProjectionFreshness::from_computed_at(updated_at, now),
            },
        }
    }

    fn make_set_event(
        data: serde_json::Value,
        session_id: Option<&str>,
        idempotency_key: &str,
    ) -> CreateEventRequest {
        CreateEventRequest {
            timestamp: Utc::now(),
            event_type: "set.logged".to_string(),
            data,
            metadata: EventMetadata {
                source: Some("api".to_string()),
                agent: Some("test".to_string()),
                device: None,
                session_id: session_id.map(|value| value.to_string()),
                idempotency_key: idempotency_key.to_string(),
            },
        }
    }

    fn make_event(
        event_type: &str,
        data: serde_json::Value,
        idempotency_key: &str,
    ) -> CreateEventRequest {
        CreateEventRequest {
            timestamp: Utc::now(),
            event_type: event_type.to_string(),
            data,
            metadata: EventMetadata {
                source: Some("api".to_string()),
                agent: Some("test".to_string()),
                device: None,
                session_id: Some("session-1".to_string()),
                idempotency_key: idempotency_key.to_string(),
            },
        }
    }

    fn make_verification(
        status: &str,
        checks: Vec<AgentReadAfterWriteCheck>,
    ) -> super::AgentWriteVerificationSummary {
        let verified_checks = checks
            .iter()
            .filter(|check| check.status == "verified")
            .count();
        super::AgentWriteVerificationSummary {
            status: status.to_string(),
            checked_at: Utc::now(),
            waited_ms: 15,
            write_path: "fresh_write".to_string(),
            required_checks: checks.len(),
            verified_checks,
            checks,
        }
    }

    async fn integration_state_if_available() -> Option<(AppState, AuthenticatedUser, Uuid)> {
        let Ok(url) = std::env::var("DATABASE_URL") else {
            return None;
        };
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect(&url)
            .await
            .ok()?;

        sqlx::migrate!("../migrations").run(&pool).await.ok()?;

        let user_id = Uuid::now_v7();
        let email = format!("viz-e2e-{}@test.local", user_id);
        sqlx::query(
            "INSERT INTO users (id, email, password_hash, display_name) VALUES ($1, $2, 'h', 'Viz Test')",
        )
        .bind(user_id)
        .bind(email)
        .execute(&pool)
        .await
        .ok()?;

        let auth = AuthenticatedUser {
            user_id,
            auth_method: AuthMethod::ApiKey {
                key_id: Uuid::now_v7(),
            },
            scopes: vec![
                "agent:read".to_string(),
                "agent:write".to_string(),
                "agent:resolve".to_string(),
            ],
        };
        Some((AppState { db: pool, signup_gate: crate::state::SignupGate::Open }, auth, user_id))
    }

    async fn upsert_test_projection(
        pool: &sqlx::PgPool,
        user_id: Uuid,
        projection_type: &str,
        key: &str,
        data: Value,
    ) {
        sqlx::query(
            r#"
            INSERT INTO projections (id, user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES ($1, $2, $3, $4, $5, 1, NULL, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                updated_at = NOW()
            "#,
        )
        .bind(Uuid::now_v7())
        .bind(user_id)
        .bind(projection_type)
        .bind(key)
        .bind(data)
        .execute(pool)
        .await
        .expect("upsert test projection");
    }

    async fn load_learning_signal_types(pool: &sqlx::PgPool, user_id: Uuid) -> Vec<String> {
        sqlx::query_scalar::<_, Option<String>>(
            r#"
            SELECT data->>'signal_type' AS signal_type
            FROM events
            WHERE user_id = $1
              AND event_type = 'learning.signal.logged'
            ORDER BY timestamp ASC, id ASC
            "#,
        )
        .bind(user_id)
        .fetch_all(pool)
        .await
        .expect("load learning signals")
        .into_iter()
        .flatten()
        .collect()
    }

    async fn insert_test_event(
        pool: &sqlx::PgPool,
        user_id: Uuid,
        event_type: &str,
        data: Value,
        metadata: Value,
    ) -> Uuid {
        let event_id = Uuid::now_v7();
        sqlx::query(
            r#"
            INSERT INTO events (id, user_id, timestamp, event_type, data, metadata)
            VALUES ($1, $2, NOW(), $3, $4, $5)
            "#,
        )
        .bind(event_id)
        .bind(user_id)
        .bind(event_type)
        .bind(data)
        .bind(metadata)
        .execute(pool)
        .await
        .expect("insert test event");
        event_id
    }

    #[test]
    fn clamp_limit_enforces_defaults_and_bounds() {
        assert_eq!(clamp_limit(None, 5, 100), 5);
        assert_eq!(clamp_limit(Some(0), 5, 100), 1);
        assert_eq!(clamp_limit(Some(101), 5, 100), 100);
        assert_eq!(clamp_limit(Some(7), 5, 100), 7);
    }

    #[test]
    fn bootstrap_user_profile_contains_onboarding_agenda() {
        let user_id = Uuid::now_v7();
        let response = bootstrap_user_profile(user_id);
        assert_eq!(response.projection.user_id, user_id);
        assert_eq!(response.projection.projection_type, "user_profile");
        assert_eq!(response.projection.key, "me");

        let agenda = response.projection.data["agenda"].as_array().unwrap();
        assert!(!agenda.is_empty());
        assert_eq!(agenda[0]["type"], "onboarding_needed");
    }

    #[test]
    fn ranking_candidate_limit_expands_pool_with_cap() {
        assert_eq!(ranking_candidate_limit(5), 25);
        assert_eq!(ranking_candidate_limit(100), 500);
    }

    #[test]
    fn context_detects_strength_intent() {
        let context = RankingContext::from_task_intent(Some("bench plateau".to_string()), None);
        assert_eq!(context.intent_class, IntentClass::Strength);
        assert!(context.intent_tokens.contains("bench"));
    }

    #[test]
    fn rank_projection_list_uses_intent_and_semantic_signals() {
        let now = Utc::now();
        let semantic_memory = make_projection_response(
            "semantic_memory",
            "overview",
            now,
            json!({
                "exercise_candidates": [
                    {
                        "term": "bankdrücken",
                        "suggested_exercise_id": "bench_press",
                        "score": 0.92,
                        "confidence": "high"
                    }
                ]
            }),
        );
        let context = RankingContext::from_task_intent(
            Some("bench plateau".to_string()),
            Some(&semantic_memory),
        );

        let candidates = vec![
            make_projection_response(
                "strength_inference",
                "squat",
                now - Duration::hours(2),
                json!({
                    "data_quality": {"sessions_used": 12, "insufficient_data": false},
                    "dynamics": {"estimated_1rm": {"confidence": 0.8}},
                    "trend": {"slope_ci95": [0.01, 0.08]}
                }),
            ),
            make_projection_response(
                "strength_inference",
                "bench_press",
                now - Duration::hours(20),
                json!({
                    "exercise_id": "bench_press",
                    "data_quality": {"sessions_used": 6, "insufficient_data": false},
                    "dynamics": {"estimated_1rm": {"confidence": 0.7}},
                    "trend": {"slope_ci95": [0.0, 0.06]}
                }),
            ),
        ];

        let ranked = rank_projection_list(candidates, 2, &context);
        assert_eq!(ranked[0].projection.key, "bench_press");
    }

    #[test]
    fn rank_projection_list_without_intent_favors_recency() {
        let now = Utc::now();
        let context = RankingContext::from_task_intent(None, None);
        let candidates = vec![
            make_projection_response(
                "exercise_progression",
                "bench_press",
                now - Duration::hours(48),
                json!({
                    "total_sets": 8,
                    "total_sessions": 3,
                    "data_quality": {"anomalies": []}
                }),
            ),
            make_projection_response(
                "exercise_progression",
                "squat",
                now - Duration::hours(2),
                json!({
                    "total_sets": 8,
                    "total_sessions": 3,
                    "data_quality": {"anomalies": []}
                }),
            ),
        ];

        let ranked = rank_projection_list(candidates, 2, &context);
        assert_eq!(ranked[0].projection.key, "squat");
    }

    #[test]
    fn clamp_verify_timeout_ms_applies_bounds() {
        assert_eq!(clamp_verify_timeout_ms(None), 1200);
        assert_eq!(clamp_verify_timeout_ms(Some(5)), 100);
        assert_eq!(clamp_verify_timeout_ms(Some(25_000)), 10_000);
    }

    #[test]
    fn normalize_read_after_write_targets_deduplicates_and_normalizes() {
        let normalized = normalize_read_after_write_targets(vec![
            AgentReadAfterWriteTarget {
                projection_type: " User_Profile ".to_string(),
                key: " Me ".to_string(),
            },
            AgentReadAfterWriteTarget {
                projection_type: "user_profile".to_string(),
                key: "me".to_string(),
            },
            AgentReadAfterWriteTarget {
                projection_type: "".to_string(),
                key: "ignored".to_string(),
            },
        ]);
        assert_eq!(
            normalized,
            vec![("user_profile".to_string(), "me".to_string())]
        );
    }

    #[test]
    fn recover_receipts_for_idempotent_retry_preserves_request_order() {
        let now = Utc::now();
        let requested = vec![
            CreateEventRequest {
                timestamp: now,
                event_type: "set.logged".to_string(),
                data: json!({"exercise_id": "squat", "reps": 5}),
                metadata: EventMetadata {
                    source: Some("api".to_string()),
                    agent: Some("test".to_string()),
                    device: None,
                    session_id: Some("s1".to_string()),
                    idempotency_key: "k-1".to_string(),
                },
            },
            CreateEventRequest {
                timestamp: now,
                event_type: "set.logged".to_string(),
                data: json!({"exercise_id": "bench", "reps": 5}),
                metadata: EventMetadata {
                    source: Some("api".to_string()),
                    agent: Some("test".to_string()),
                    device: None,
                    session_id: Some("s1".to_string()),
                    idempotency_key: "k-2".to_string(),
                },
            },
        ];

        let mut recovered_by_key = std::collections::HashMap::new();
        recovered_by_key.insert(
            "k-2".to_string(),
            AgentWriteReceipt {
                event_id: Uuid::now_v7(),
                event_type: "set.logged".to_string(),
                idempotency_key: "k-2".to_string(),
                event_timestamp: now,
            },
        );
        recovered_by_key.insert(
            "k-1".to_string(),
            AgentWriteReceipt {
                event_id: Uuid::now_v7(),
                event_type: "set.logged".to_string(),
                idempotency_key: "k-1".to_string(),
                event_timestamp: now,
            },
        );

        let recovered = recover_receipts_for_idempotent_retry(&requested, &recovered_by_key);
        assert_eq!(recovered.len(), 2);
        assert_eq!(recovered[0].idempotency_key, "k-1");
        assert_eq!(recovered[1].idempotency_key, "k-2");
    }

    #[test]
    fn recover_receipts_for_idempotent_retry_skips_missing_keys() {
        let now = Utc::now();
        let requested = vec![CreateEventRequest {
            timestamp: now,
            event_type: "set.logged".to_string(),
            data: json!({"exercise_id": "squat", "reps": 5}),
            metadata: EventMetadata {
                source: Some("api".to_string()),
                agent: Some("test".to_string()),
                device: None,
                session_id: Some("s1".to_string()),
                idempotency_key: "k-missing".to_string(),
            },
        }];
        let recovered =
            recover_receipts_for_idempotent_retry(&requested, &std::collections::HashMap::new());
        assert!(recovered.is_empty());
    }

    #[test]
    fn onboarding_close_requirements_accept_deferred_baseline_with_timezone() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "unit_system": "metric",
                        "timezone": "Europe/Berlin"
                    },
                    "interview_coverage": [
                        {"area": "training_background", "status": "covered"},
                        {"area": "baseline_profile", "status": "deferred"},
                        {"area": "unit_preferences", "status": "covered"}
                    ]
                }
            }),
        );
        let missing = missing_onboarding_close_requirements(Some(&profile));
        assert!(missing.is_empty());
    }

    #[test]
    fn onboarding_close_requirements_flag_timezone_when_missing() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "unit_system": "metric"
                    },
                    "interview_coverage": [
                        {"area": "training_background", "status": "covered"},
                        {"area": "baseline_profile", "status": "covered"},
                        {"area": "unit_preferences", "status": "covered"}
                    ]
                }
            }),
        );
        let missing = missing_onboarding_close_requirements(Some(&profile));
        assert!(
            missing
                .iter()
                .any(|item| item == "preference.timezone.missing")
        );
    }

    #[test]
    fn workflow_gate_blocks_planning_drift_before_phase_close() {
        let state = AgentWorkflowState {
            onboarding_closed: false,
            override_active: false,
            missing_close_requirements: vec!["coverage.baseline_profile.uncovered".to_string()],
            legacy_planning_history: false,
        };
        let events = vec![make_event(
            "training_plan.created",
            json!({"name": "Starter Plan"}),
            "plan-k-1",
        )];
        let gate = workflow_gate_from_request(&events, &state);
        assert_eq!(gate.status, "blocked");
        assert_eq!(gate.phase, "onboarding");
        assert_eq!(gate.transition, "none");
        assert!(
            gate.planning_event_types
                .iter()
                .any(|event_type| event_type == "training_plan.created")
        );
    }

    #[test]
    fn workflow_gate_allows_valid_onboarding_close_transition() {
        let state = AgentWorkflowState {
            onboarding_closed: false,
            override_active: false,
            missing_close_requirements: Vec::new(),
            legacy_planning_history: false,
        };
        let events = vec![
            make_event(
                WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE,
                json!({"reason": "onboarding complete"}),
                "wf-close-k-1",
            ),
            make_event(
                "training_plan.created",
                json!({"name": "Starter Plan"}),
                "plan-k-2",
            ),
        ];
        let gate = workflow_gate_from_request(&events, &state);
        assert_eq!(gate.status, "allowed");
        assert_eq!(gate.transition, "onboarding_closed");
        assert_eq!(gate.phase, "planning");
        assert!(gate.onboarding_closed);
    }

    #[test]
    fn workflow_gate_allows_explicit_override_path() {
        let state = AgentWorkflowState {
            onboarding_closed: false,
            override_active: false,
            missing_close_requirements: vec!["coverage.unit_preferences.uncovered".to_string()],
            legacy_planning_history: false,
        };
        let events = vec![
            make_event(
                WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE,
                json!({"reason": "user asked for plan now"}),
                "wf-override-k-1",
            ),
            make_event(
                "training_plan.updated",
                json!({"name": "Adjusted Plan"}),
                "plan-k-3",
            ),
        ];
        let gate = workflow_gate_from_request(&events, &state);
        assert_eq!(gate.status, "allowed");
        assert_eq!(gate.transition, "override");
        assert!(gate.override_used);
        assert_eq!(gate.phase, "onboarding");
    }

    #[test]
    fn workflow_gate_allows_legacy_compatibility_transition_when_requirements_met() {
        let state = AgentWorkflowState {
            onboarding_closed: false,
            override_active: false,
            missing_close_requirements: Vec::new(),
            legacy_planning_history: true,
        };
        let events = vec![make_event(
            "training_plan.created",
            json!({"name": "Starter Plan"}),
            "plan-k-legacy-1",
        )];
        let gate = workflow_gate_from_request(&events, &state);
        assert_eq!(gate.status, "allowed");
        assert_eq!(gate.transition, "onboarding_closed");
        assert_eq!(gate.phase, "planning");
        assert!(gate.onboarding_closed);
        assert!(
            gate.message
                .contains("legacy compatibility; onboarding close marker will be auto-recorded")
        );
    }

    #[test]
    fn auto_onboarding_close_event_uses_deterministic_idempotency_seed() {
        let events = vec![
            make_event("training_plan.created", json!({"name": "A"}), "plan-k-1"),
            make_event("training_plan.updated", json!({"name": "B"}), "plan-k-2"),
        ];
        let first = build_auto_onboarding_close_event(&events);
        let second = build_auto_onboarding_close_event(&events);

        assert_eq!(first.event_type, WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE);
        assert_eq!(
            first.metadata.idempotency_key,
            second.metadata.idempotency_key
        );
        assert_eq!(
            first.data.get("closed_by").and_then(Value::as_str),
            Some("system_auto")
        );
    }

    #[test]
    fn visualization_policy_triggers_for_plan_vs_actual_intent() {
        let decision = visualization_policy_decision(
            "show plan vs actual adherence for the next 4 weeks",
            None,
            None,
            2,
        );
        assert_eq!(decision.status, "visualize");
        assert_eq!(decision.trigger, "plan_vs_actual");
    }

    #[test]
    fn visualization_policy_skips_when_no_trigger_is_present() {
        let decision =
            visualization_policy_decision("what is my latest bodyweight entry", None, None, 1);
        assert_eq!(decision.status, "skipped");
        assert_eq!(decision.trigger, "none");
    }

    #[test]
    fn visualization_source_binding_rejects_unresolvable_json_path() {
        let source = AgentVisualizationDataSource {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
            json_path: Some("weekly_summary.0.missing_field".to_string()),
        };
        let projection = make_projection_response(
            "training_timeline",
            "overview",
            Utc::now(),
            json!({
                "weekly_summary": [
                    {"week": "2026-W06", "total_volume_kg": 1234.0}
                ]
            }),
        );

        let error = bind_visualization_source(&source, &projection)
            .expect_err("missing json_path field must fail source binding");
        assert!(error.contains("was not resolvable"));
    }

    #[test]
    fn visualization_fallback_returns_ascii_when_rich_rendering_is_disabled() {
        let spec = normalize_visualization_spec(AgentVisualizationSpec {
            format: "mermaid".to_string(),
            purpose: "Compare weekly training load".to_string(),
            title: None,
            timezone: None,
            data_sources: vec![AgentVisualizationDataSource {
                projection_type: "training_timeline".to_string(),
                key: "overview".to_string(),
                json_path: Some("weekly_summary".to_string()),
            }],
        })
        .expect("spec normalization should succeed");
        let resolved = vec![AgentVisualizationResolvedSource {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
            json_path: Some("weekly_summary".to_string()),
            projection_version: 3,
            projection_last_event_id: None,
            value: json!([{"week": "2026-W06", "total_volume_kg": 1234.0}]),
        }];
        let timezone = AgentVisualizationTimezoneContext {
            timezone: "UTC".to_string(),
            assumed: true,
            source: "fallback_utc".to_string(),
        };

        let (status, output, fallback_output, warnings) =
            build_visualization_outputs(&spec, &resolved, &timezone, false, None);
        assert_eq!(status, "fallback");
        assert_eq!(output.format, "ascii");
        assert!(fallback_output.is_none());
        assert!(
            warnings
                .iter()
                .any(|warning| warning.contains("UTC fallback"))
        );
    }

    #[tokio::test]
    async fn visualization_resolve_e2e_visualize_returns_resolved_sources_and_telemetry() {
        let Some((state, auth, user_id)) = integration_state_if_available().await else {
            return;
        };

        upsert_test_projection(
            &state.db,
            user_id,
            "user_profile",
            "me",
            json!({
                "user": {
                    "preferences": {
                        "timezone": "Europe/Berlin"
                    }
                }
            }),
        )
        .await;
        upsert_test_projection(
            &state.db,
            user_id,
            "training_timeline",
            "overview",
            json!({
                "weekly_summary": [
                    {"week": "2026-W06", "total_volume_kg": 1200.0},
                    {"week": "2026-W07", "total_volume_kg": 1320.0}
                ]
            }),
        )
        .await;

        let req = AgentResolveVisualizationRequest {
            task_intent: "compare last 4 weeks volume vs plan".to_string(),
            user_preference_override: None,
            complexity_hint: None,
            allow_rich_rendering: true,
            visualization_spec: Some(AgentVisualizationSpec {
                format: "chart".to_string(),
                purpose: "4-week volume trend".to_string(),
                title: Some("Volume vs plan".to_string()),
                timezone: None,
                data_sources: vec![
                    AgentVisualizationDataSource {
                        projection_type: "training_timeline".to_string(),
                        key: "overview".to_string(),
                        json_path: Some("weekly_summary.0.total_volume_kg".to_string()),
                    },
                    AgentVisualizationDataSource {
                        projection_type: "training_timeline".to_string(),
                        key: "overview".to_string(),
                        json_path: Some("weekly_summary.1.total_volume_kg".to_string()),
                    },
                ],
            }),
            telemetry_session_id: Some("viz-e2e-visualize".to_string()),
        };

        let response = resolve_visualization(State(state.clone()), auth.clone(), Json(req))
            .await
            .expect("resolve visualization should succeed")
            .0;

        assert_eq!(response.policy.status, "visualize");
        assert_eq!(response.resolved_sources.len(), 2);
        assert_eq!(response.resolved_sources[0].value, json!(1200.0));
        assert_eq!(response.resolved_sources[1].value, json!(1320.0));
        assert_eq!(response.output.format, "chart");
        assert!(
            response
                .fallback_output
                .as_ref()
                .is_some_and(|output| output.format == "ascii")
        );
        assert_eq!(
            response
                .timezone_context
                .as_ref()
                .map(|context| context.source.as_str()),
            Some("user_profile.preference")
        );
        assert!(
            response
                .telemetry_signal_types
                .iter()
                .any(|signal| signal == "viz_source_bound")
        );
        assert!(
            response
                .telemetry_signal_types
                .iter()
                .any(|signal| signal == "viz_shown")
        );

        let signal_types = load_learning_signal_types(&state.db, user_id).await;
        assert!(
            signal_types
                .iter()
                .any(|signal| signal == "viz_source_bound")
        );
        assert!(signal_types.iter().any(|signal| signal == "viz_shown"));
    }

    #[tokio::test]
    async fn visualization_resolve_e2e_invalid_json_path_returns_validation_with_docs_hint() {
        let Some((state, auth, user_id)) = integration_state_if_available().await else {
            return;
        };

        upsert_test_projection(
            &state.db,
            user_id,
            "training_timeline",
            "overview",
            json!({
                "weekly_summary": [
                    {"week": "2026-W06", "total_volume_kg": 1200.0}
                ]
            }),
        )
        .await;

        let req = AgentResolveVisualizationRequest {
            task_intent: "compare training trend".to_string(),
            user_preference_override: None,
            complexity_hint: None,
            allow_rich_rendering: true,
            visualization_spec: Some(AgentVisualizationSpec {
                format: "table".to_string(),
                purpose: "Weekly comparison".to_string(),
                title: None,
                timezone: None,
                data_sources: vec![AgentVisualizationDataSource {
                    projection_type: "training_timeline".to_string(),
                    key: "overview".to_string(),
                    json_path: Some("weekly_summary.0.missing_field".to_string()),
                }],
            }),
            telemetry_session_id: Some("viz-e2e-invalid".to_string()),
        };

        let error = resolve_visualization(State(state), auth, Json(req))
            .await
            .expect_err("invalid json_path must fail");

        match error {
            AppError::Validation {
                field, docs_hint, ..
            } => {
                assert_eq!(field.as_deref(), Some("visualization_spec.data_sources"));
                let hint = docs_hint.unwrap_or_default();
                assert!(hint.contains("json_path"));
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[tokio::test]
    async fn visualization_resolve_e2e_allow_rich_false_returns_ascii_equivalent_and_fallback_signal()
     {
        let Some((state, auth, user_id)) = integration_state_if_available().await else {
            return;
        };

        upsert_test_projection(
            &state.db,
            user_id,
            "user_profile",
            "me",
            json!({
                "user": {
                    "preferences": {
                        "timezone": "Europe/Berlin"
                    }
                }
            }),
        )
        .await;
        upsert_test_projection(
            &state.db,
            user_id,
            "training_timeline",
            "overview",
            json!({
                "weekly_summary": [
                    {"week": "2026-W06", "total_volume_kg": 1200.0}
                ]
            }),
        )
        .await;

        let base_spec = AgentVisualizationSpec {
            format: "mermaid".to_string(),
            purpose: "Compare weekly training load".to_string(),
            title: None,
            timezone: None,
            data_sources: vec![AgentVisualizationDataSource {
                projection_type: "training_timeline".to_string(),
                key: "overview".to_string(),
                json_path: Some("weekly_summary".to_string()),
            }],
        };

        let rich_response = resolve_visualization(
            State(state.clone()),
            auth.clone(),
            Json(AgentResolveVisualizationRequest {
                task_intent: "compare weekly trend".to_string(),
                user_preference_override: None,
                complexity_hint: None,
                allow_rich_rendering: true,
                visualization_spec: Some(base_spec.clone()),
                telemetry_session_id: Some("viz-e2e-rich".to_string()),
            }),
        )
        .await
        .expect("rich rendering should succeed")
        .0;
        let rich_ascii = rich_response
            .fallback_output
            .as_ref()
            .map(|output| output.content.clone())
            .expect("rich output must include deterministic ascii fallback");

        let fallback_response = resolve_visualization(
            State(state.clone()),
            auth.clone(),
            Json(AgentResolveVisualizationRequest {
                task_intent: "compare weekly trend".to_string(),
                user_preference_override: None,
                complexity_hint: None,
                allow_rich_rendering: false,
                visualization_spec: Some(base_spec),
                telemetry_session_id: Some("viz-e2e-fallback".to_string()),
            }),
        )
        .await
        .expect("fallback rendering should succeed")
        .0;

        assert_eq!(fallback_response.policy.status, "fallback");
        assert_eq!(fallback_response.output.format, "ascii");
        assert!(fallback_response.fallback_output.is_none());
        assert_eq!(fallback_response.output.content, rich_ascii);
        assert!(
            fallback_response
                .telemetry_signal_types
                .iter()
                .any(|signal| signal == "viz_fallback_used")
        );

        let signal_types = load_learning_signal_types(&state.db, user_id).await;
        assert!(
            signal_types
                .iter()
                .any(|signal| signal == "viz_fallback_used")
        );
    }

    #[tokio::test]
    async fn visualization_resolve_e2e_policy_skip_emits_viz_skipped_signal() {
        let Some((state, auth, user_id)) = integration_state_if_available().await else {
            return;
        };

        let response = resolve_visualization(
            State(state.clone()),
            auth.clone(),
            Json(AgentResolveVisualizationRequest {
                task_intent: "what is my latest bodyweight entry".to_string(),
                user_preference_override: None,
                complexity_hint: None,
                allow_rich_rendering: true,
                visualization_spec: None,
                telemetry_session_id: Some("viz-e2e-skipped".to_string()),
            }),
        )
        .await
        .expect("skip path should succeed")
        .0;

        assert_eq!(response.policy.status, "skipped");
        assert!(response.resolved_sources.is_empty());
        assert_eq!(response.output.format, "text");
        assert!(
            response
                .telemetry_signal_types
                .iter()
                .any(|signal| signal == "viz_skipped")
        );

        let signal_types = load_learning_signal_types(&state.db, user_id).await;
        assert!(signal_types.iter().any(|signal| signal == "viz_skipped"));
    }

    #[tokio::test]
    async fn evidence_lineage_endpoint_returns_claims_for_target_event() {
        let Some((state, auth, user_id)) = integration_state_if_available().await else {
            return;
        };

        let target_event_id = insert_test_event(
            &state.db,
            user_id,
            "set.logged",
            json!({
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "utterance": "3x5 squat, rest 90 sec"
            }),
            json!({"idempotency_key": format!("target-{}", Uuid::now_v7())}),
        )
        .await;

        let claim_id = "claim_test_evidence_01";
        insert_test_event(
            &state.db,
            user_id,
            "evidence.claim.logged",
            json!({
                "claim_id": claim_id,
                "claim_type": "set_context.rest_seconds",
                "value": 90,
                "unit": "seconds",
                "scope": {"level": "set", "event_type": "set.logged", "exercise_id": "barbell_back_squat"},
                "confidence": 0.95,
                "provenance": {
                    "source_field": "utterance",
                    "source_text": "3x5 squat, rest 90 sec",
                    "source_text_span": {"start": 10, "end": 21, "text": "rest 90 sec"},
                    "parser_version": "mention_parser.v1"
                },
                "lineage": {
                    "event_id": target_event_id,
                    "event_type": "set.logged",
                    "lineage_type": "supports"
                }
            }),
            json!({"idempotency_key": format!("evidence-{}", Uuid::now_v7())}),
        )
        .await;

        let response = super::get_event_evidence_lineage(State(state), auth, Path(target_event_id))
            .await
            .expect("evidence endpoint should succeed")
            .0;

        assert_eq!(response.event_id, target_event_id);
        assert_eq!(response.claims.len(), 1);
        assert_eq!(response.claims[0].claim_id, claim_id);
        assert_eq!(response.claims[0].claim_type, "set_context.rest_seconds");
        assert_eq!(
            response.claims[0].provenance["source_text_span"]["text"],
            json!("rest 90 sec")
        );
        assert_eq!(
            response.claims[0].lineage["event_id"],
            json!(target_event_id)
        );
    }

    #[test]
    fn session_audit_is_clean_when_no_missing_mention_bound_fields() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_set_event(
            json!({
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "rest_seconds": 90,
                "notes": "rest 90 sec"
            }),
            Some("session-1"),
            "k-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "clean");
        assert_eq!(artifacts.summary.mismatch_detected, 0);
        assert_eq!(artifacts.summary.mismatch_repaired, 0);
        assert_eq!(artifacts.summary.mismatch_unresolved, 0);
        assert!(artifacts.summary.clarification_question.is_none());
        assert!(artifacts.repair_events.is_empty());
        assert!(artifacts.telemetry_events.is_empty());
    }

    #[test]
    fn session_audit_auto_repairs_high_confidence_mismatch() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_set_event(
            json!({
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "notes": "rest 90 sec"
            }),
            Some("session-1"),
            "k-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "repaired");
        assert_eq!(artifacts.summary.mismatch_detected, 1);
        assert_eq!(artifacts.summary.mismatch_repaired, 1);
        assert_eq!(artifacts.summary.mismatch_unresolved, 0);
        assert!(artifacts.summary.clarification_question.is_none());
        assert_eq!(artifacts.repair_events.len(), 1);
        assert_eq!(artifacts.repair_events[0].event_type, "set.corrected");
        assert_eq!(
            artifacts.repair_events[0].data["target_event_id"],
            json!(receipts[0].event_id)
        );
        assert_eq!(
            artifacts.repair_events[0].data["changed_fields"]["rest_seconds"]["value"]
                .as_f64()
                .unwrap_or_default(),
            90.0
        );

        let signal_types: Vec<String> = artifacts
            .telemetry_events
            .iter()
            .filter_map(|event| {
                event
                    .data
                    .get("signal_type")
                    .and_then(|value| value.as_str())
                    .map(|value| value.to_string())
            })
            .collect();
        assert!(signal_types.iter().any(|s| s == "mismatch_detected"));
        assert!(signal_types.iter().any(|s| s == "mismatch_repaired"));
        assert!(!signal_types.iter().any(|s| s == "mismatch_unresolved"));
    }

    #[test]
    fn session_audit_requires_clarification_for_conflicting_mentions() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_set_event(
            json!({
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "notes": "rest 60 sec",
                "context_text": "rest 90 sec"
            }),
            Some("session-1"),
            "k-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "needs_clarification");
        assert_eq!(artifacts.summary.mismatch_detected, 1);
        assert_eq!(artifacts.summary.mismatch_repaired, 0);
        assert_eq!(artifacts.summary.mismatch_unresolved, 1);
        assert!(artifacts.summary.clarification_question.is_some());
        assert!(artifacts.repair_events.is_empty());

        let signal_types: Vec<String> = artifacts
            .telemetry_events
            .iter()
            .filter_map(|event| {
                event
                    .data
                    .get("signal_type")
                    .and_then(|value| value.as_str())
                    .map(|value| value.to_string())
            })
            .collect();
        assert!(signal_types.iter().any(|s| s == "mismatch_detected"));
        assert!(signal_types.iter().any(|s| s == "mismatch_unresolved"));
        assert!(!signal_types.iter().any(|s| s == "mismatch_repaired"));
    }

    #[test]
    fn session_audit_session_feedback_scale_guard_auto_repairs() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_event(
            "session.completed",
            json!({
                "enjoyment": 8,
                "perceived_quality": 9,
                "perceived_exertion": 7,
                "notes": "felt good and strong"
            }),
            "k-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "session.completed".to_string(),
            idempotency_key: "k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "repaired");
        assert_eq!(artifacts.summary.mismatch_detected, 2);
        assert_eq!(artifacts.summary.mismatch_repaired, 2);
        assert_eq!(artifacts.summary.mismatch_unresolved, 0);
        assert!(
            artifacts
                .summary
                .mismatch_classes
                .iter()
                .any(|c| c == "scale_normalized_to_five")
        );
        assert_eq!(artifacts.repair_events.len(), 2);
        assert_eq!(artifacts.repair_events[0].event_type, "event.retracted");
        assert_eq!(artifacts.repair_events[1].event_type, "session.completed");
        assert_eq!(artifacts.repair_events[1].data["enjoyment"], json!(4.0));
        assert_eq!(
            artifacts.repair_events[1].data["perceived_quality"],
            json!(4.5)
        );
    }

    #[test]
    fn session_audit_session_feedback_contradiction_needs_clarification() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_event(
            "session.completed",
            json!({
                "enjoyment": 5,
                "perceived_quality": 5,
                "perceived_exertion": 8,
                "notes": "the session felt bad and awful"
            }),
            "k-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "session.completed".to_string(),
            idempotency_key: "k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "needs_clarification");
        assert!(artifacts.summary.mismatch_detected >= 1);
        assert!(artifacts.summary.mismatch_unresolved >= 1);
        assert!(
            artifacts
                .summary
                .mismatch_classes
                .iter()
                .any(|c| c == "narrative_structured_contradiction")
        );
        assert!(artifacts.summary.clarification_question.is_some());
        assert!(artifacts.repair_events.is_empty());
    }

    #[test]
    fn session_audit_session_feedback_clean_when_consistent() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_event(
            "session.completed",
            json!({
                "enjoyment": 4,
                "perceived_quality": 4,
                "perceived_exertion": 7,
                "notes": "felt good and focused"
            }),
            "k-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "session.completed".to_string(),
            idempotency_key: "k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "clean");
        assert_eq!(artifacts.summary.mismatch_detected, 0);
        assert_eq!(artifacts.summary.mismatch_repaired, 0);
        assert_eq!(artifacts.summary.mismatch_unresolved, 0);
        assert!(artifacts.summary.mismatch_classes.is_empty());
        assert!(artifacts.repair_events.is_empty());
    }

    #[test]
    fn session_feedback_certainty_contract_accepts_valid_states() {
        let events = vec![make_event(
            "session.completed",
            json!({
                "enjoyment": 4,
                "enjoyment_state": "confirmed",
                "perceived_quality_state": "unresolved",
                "perceived_quality_unresolved_reason": "not asked yet",
                "perceived_exertion": 7,
                "perceived_exertion_source": "explicit"
            }),
            "k-1",
        )];

        assert!(validate_session_feedback_certainty_contract(&events).is_ok());
    }

    #[test]
    fn session_feedback_certainty_contract_rejects_inferred_without_evidence() {
        let events = vec![make_event(
            "session.completed",
            json!({
                "enjoyment": 4,
                "enjoyment_state": "inferred"
            }),
            "k-1",
        )];

        let err = validate_session_feedback_certainty_contract(&events).expect_err("must fail");
        match err {
            AppError::PolicyViolation { code, field, .. } => {
                assert_eq!(code, "session_feedback_inferred_missing_evidence");
                assert_eq!(
                    field.as_deref(),
                    Some("events[0].data.enjoyment_evidence_claim_id")
                );
            }
            other => panic!("unexpected error variant: {other:?}"),
        }
    }

    #[test]
    fn session_feedback_certainty_contract_rejects_unresolved_with_value() {
        let events = vec![make_event(
            "session.completed",
            json!({
                "perceived_quality": 4,
                "perceived_quality_state": "unresolved",
                "perceived_quality_unresolved_reason": "user skipped question"
            }),
            "k-1",
        )];

        let err = validate_session_feedback_certainty_contract(&events).expect_err("must fail");
        match err {
            AppError::PolicyViolation { code, field, .. } => {
                assert_eq!(code, "session_feedback_unresolved_has_value");
                assert_eq!(field.as_deref(), Some("events[0].data.perceived_quality"));
            }
            other => panic!("unexpected error variant: {other:?}"),
        }
    }

    #[test]
    fn session_feedback_certainty_contract_matrix_fuzz() {
        let states: [Option<&str>; 4] = [None, Some("confirmed"), Some("inferred"), Some("unresolved")];
        let sources: [Option<&str>; 5] = [
            None,
            Some("explicit"),
            Some("user_confirmed"),
            Some("estimated"),
            Some("inferred"),
        ];

        for state in states {
            for source in sources {
                for has_value in [false, true] {
                    for has_evidence in [false, true] {
                        for has_reason in [false, true] {
                            let mut payload = serde_json::Map::new();
                            if has_value {
                                payload.insert("enjoyment".to_string(), json!(4));
                            }
                            if let Some(state_value) = state {
                                payload.insert("enjoyment_state".to_string(), json!(state_value));
                            }
                            if let Some(source_value) = source {
                                payload.insert("enjoyment_source".to_string(), json!(source_value));
                            }
                            if has_evidence {
                                payload.insert(
                                    "enjoyment_evidence_claim_id".to_string(),
                                    json!("claim-1"),
                                );
                            }
                            if has_reason {
                                payload.insert(
                                    "enjoyment_unresolved_reason".to_string(),
                                    json!("need clarification"),
                                );
                            }

                            let events = vec![make_event(
                                "session.completed",
                                Value::Object(payload),
                                "k-fuzz",
                            )];
                            let result = validate_session_feedback_certainty_contract(&events);

                            let inferred_path = matches!(state, Some("inferred"))
                                || matches!(source, Some("inferred"));
                            let invalid = (matches!(state, Some("confirmed")) && !has_value)
                                || (inferred_path && (!has_value || !has_evidence))
                                || (matches!(state, Some("unresolved"))
                                    && (has_value || !has_reason));

                            if invalid {
                                assert!(
                                    result.is_err(),
                                    "expected invalid combo to fail: state={state:?} source={source:?} value={has_value} evidence={has_evidence} reason={has_reason}"
                                );
                            } else {
                                assert!(
                                    result.is_ok(),
                                    "expected valid combo to pass: state={state:?} source={source:?} value={has_value} evidence={has_evidence} reason={has_reason}"
                                );
                            }
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn session_audit_respects_policy_when_auto_repair_is_disabled() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_set_event(
            json!({
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "notes": "rest 90 sec"
            }),
            Some("session-1"),
            "k-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let mut policy = default_autonomy_policy();
        policy.repair_auto_apply_enabled = false;
        policy.require_confirmation_for_repairs = true;

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "needs_clarification");
        assert_eq!(artifacts.summary.mismatch_detected, 1);
        assert_eq!(artifacts.summary.mismatch_repaired, 0);
        assert_eq!(artifacts.summary.mismatch_unresolved, 1);
        assert!(artifacts.summary.clarification_question.is_some());
        assert!(artifacts.repair_events.is_empty());
    }

    #[test]
    fn repair_feedback_default_view_hides_technical_details() {
        let user_id = Uuid::now_v7();
        let event_id = Uuid::now_v7();
        let requested = vec![make_set_event(
            json!({
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "notes": "rest 90 sec"
            }),
            Some("session-1"),
            "k-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id,
            event_type: "set.logged".to_string(),
            idempotency_key: "k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let repair_receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.corrected".to_string(),
            idempotency_key: "repair-k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();
        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);

        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(2),
            observed_last_event_id: Some(event_id),
            detail: "ok".to_string(),
        }];
        let verification = make_verification("verified", checks.clone());
        let guard = build_claim_guard(
            &receipts,
            requested.len(),
            &checks,
            &[],
            default_autonomy_policy(),
        );

        let feedback = build_repair_feedback(
            false,
            &artifacts.summary,
            &artifacts.repair_events,
            &repair_receipts,
            requested.len(),
            &verification,
            &guard,
        );

        assert_eq!(feedback.status, "repaired");
        assert!(feedback.summary.contains("automatisch"));
        assert_eq!(feedback.receipt.changed_fields_count, 1);
        assert!(feedback.technical.is_none());
        assert!(feedback.undo.is_some());
    }

    #[test]
    fn repair_feedback_power_view_includes_technical_details() {
        let user_id = Uuid::now_v7();
        let event_id = Uuid::now_v7();
        let requested = vec![make_set_event(
            json!({
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "notes": "rest 90 sec"
            }),
            Some("session-1"),
            "k-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id,
            event_type: "set.logged".to_string(),
            idempotency_key: "k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let repair_receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.corrected".to_string(),
            idempotency_key: "repair-k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();
        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);

        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(2),
            observed_last_event_id: Some(event_id),
            detail: "ok".to_string(),
        }];
        let verification = make_verification("verified", checks.clone());
        let guard = build_claim_guard(
            &receipts,
            requested.len(),
            &checks,
            &[],
            default_autonomy_policy(),
        );

        let feedback = build_repair_feedback(
            true,
            &artifacts.summary,
            &artifacts.repair_events,
            &repair_receipts,
            requested.len(),
            &verification,
            &guard,
        );

        let technical = feedback
            .technical
            .expect("technical details expected for power-user view");
        assert!(!technical.repair_event_ids.is_empty());
        assert!(!technical.field_diffs.is_empty());
        assert!(
            technical
                .command_trace
                .iter()
                .any(|step| step == "session_audit.apply_set_corrected")
        );
    }

    #[test]
    fn repair_feedback_exposes_undo_events_for_last_repair_batch() {
        let summary = super::AgentSessionAuditSummary {
            status: "repaired".to_string(),
            mismatch_detected: 1,
            mismatch_repaired: 1,
            mismatch_unresolved: 0,
            mismatch_classes: vec!["missing_mention_bound_field".to_string()],
            clarification_question: None,
        };
        let repair_receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.corrected".to_string(),
            idempotency_key: "repair-k-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(2),
            observed_last_event_id: Some(Uuid::now_v7()),
            detail: "ok".to_string(),
        }];
        let verification = make_verification("verified", checks.clone());
        let guard = build_claim_guard(&[], 0, &checks, &[], default_autonomy_policy());

        let feedback = build_repair_feedback(
            false,
            &summary,
            &[],
            &repair_receipts,
            0,
            &verification,
            &guard,
        );

        let undo = feedback.undo.expect("undo expected");
        assert!(undo.available);
        assert_eq!(undo.events.len(), 1);
        assert_eq!(undo.events[0].event_type, "event.retracted");
        assert_eq!(
            undo.events[0].data["retracted_event_id"],
            json!(repair_receipts[0].event_id)
        );
    }

    #[test]
    fn claim_guard_is_verified_only_when_receipts_and_readback_complete() {
        let event_id = Uuid::now_v7();
        let receipts = vec![AgentWriteReceipt {
            event_id,
            event_type: "set.logged".to_string(),
            idempotency_key: "abc-123".to_string(),
            event_timestamp: Utc::now(),
        }];
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(4),
            observed_last_event_id: Some(event_id),
            detail: "ok".to_string(),
        }];

        let guard = build_claim_guard(&receipts, 1, &checks, &[], default_autonomy_policy());
        assert!(guard.allow_saved_claim);
        assert_eq!(guard.claim_status, "saved_verified");
        assert!(guard.uncertainty_markers.is_empty());
        assert!(guard.next_action_confirmation_prompt.is_none());
    }

    #[test]
    fn claim_guard_returns_deferred_markers_when_verification_pending() {
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "abc-123".to_string(),
            event_timestamp: Utc::now(),
        }];
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "pending".to_string(),
            observed_projection_version: None,
            observed_last_event_id: None,
            detail: "pending".to_string(),
        }];
        let warnings = vec![BatchEventWarning {
            event_index: 0,
            field: "weight_kg".to_string(),
            message: "warning".to_string(),
            severity: "warning".to_string(),
        }];

        let guard = build_claim_guard(&receipts, 1, &checks, &warnings, default_autonomy_policy());
        assert!(!guard.allow_saved_claim);
        assert_eq!(guard.claim_status, "pending");
        assert!(
            guard
                .uncertainty_markers
                .iter()
                .any(|marker| marker == "read_after_write_unverified")
        );
        assert!(
            guard
                .deferred_markers
                .iter()
                .any(|marker| marker == "defer_saved_claim_until_projection_readback")
        );
        assert!(
            guard
                .uncertainty_markers
                .iter()
                .any(|marker| marker == "plausibility_warnings_present")
        );
    }

    #[test]
    fn claim_guard_marks_autonomy_throttle_when_policy_requires_confirmation() {
        let event_id = Uuid::now_v7();
        let receipts = vec![AgentWriteReceipt {
            event_id,
            event_type: "set.logged".to_string(),
            idempotency_key: "abc-123".to_string(),
            event_timestamp: Utc::now(),
        }];
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(4),
            observed_last_event_id: Some(event_id),
            detail: "ok".to_string(),
        }];
        let mut policy = default_autonomy_policy();
        policy.slo_status = "degraded".to_string();
        policy.throttle_active = true;
        policy.max_scope_level = "strict".to_string();
        policy.require_confirmation_for_non_trivial_actions = true;

        let guard = build_claim_guard(&receipts, 1, &checks, &[], policy);
        assert!(guard.allow_saved_claim);
        assert_eq!(guard.claim_status, "saved_verified");
        assert_eq!(guard.autonomy_policy.slo_status, "degraded");
        assert!(
            guard
                .next_action_confirmation_prompt
                .as_deref()
                .unwrap_or("")
                .len()
                > 10
        );
        assert!(
            guard
                .uncertainty_markers
                .iter()
                .any(|marker| marker == "autonomy_throttled_by_integrity_slo")
        );
    }

    #[test]
    fn claim_guard_returns_failed_when_receipts_are_incomplete() {
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(1),
            observed_last_event_id: Some(Uuid::now_v7()),
            detail: "ok".to_string(),
        }];

        let guard = build_claim_guard(&[], 1, &checks, &[], default_autonomy_policy());
        assert!(!guard.allow_saved_claim);
        assert_eq!(guard.claim_status, "failed");
        assert!(
            guard
                .deferred_markers
                .iter()
                .any(|marker| marker == "defer_saved_claim_until_receipt_complete")
        );
    }

    #[test]
    fn reliability_ux_state_saved_for_verified_without_inference() {
        let event_id = Uuid::now_v7();
        let receipts = vec![AgentWriteReceipt {
            event_id,
            event_type: "set.logged".to_string(),
            idempotency_key: "abc-123".to_string(),
            event_timestamp: Utc::now(),
        }];
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(1),
            observed_last_event_id: Some(event_id),
            detail: "ok".to_string(),
        }];
        let guard = build_claim_guard(&receipts, 1, &checks, &[], default_autonomy_policy());
        let summary = super::AgentSessionAuditSummary {
            status: "clean".to_string(),
            mismatch_detected: 0,
            mismatch_repaired: 0,
            mismatch_unresolved: 0,
            mismatch_classes: vec![],
            clarification_question: None,
        };

        let ux = build_reliability_ux(&guard, &summary, vec![]);
        assert_eq!(ux.state, "saved");
        assert!(ux.assistant_phrase.contains("Saved"));
        assert!(ux.inferred_facts.is_empty());
        assert!(ux.clarification_question.is_none());
    }

    #[test]
    fn reliability_ux_state_inferred_when_evidence_has_confidence_and_provenance() {
        let evidence_event = CreateEventRequest {
            timestamp: Utc::now(),
            event_type: "evidence.claim.logged".to_string(),
            data: json!({
                "claim_type": "set_context.rest_seconds",
                "confidence": 0.95,
                "provenance": {
                    "source_text_span": {
                        "text": "rest 90 sec"
                    }
                }
            }),
            metadata: EventMetadata {
                source: Some("agent_write_with_proof".to_string()),
                agent: Some("api".to_string()),
                device: None,
                session_id: Some("session-1".to_string()),
                idempotency_key: "evidence-1".to_string(),
            },
        };
        let inferred_facts = collect_reliability_inferred_facts(&[evidence_event], &[]);
        assert_eq!(inferred_facts.len(), 1);
        assert_eq!(inferred_facts[0].field, "set_context.rest_seconds");
        assert!((inferred_facts[0].confidence - 0.95).abs() < f64::EPSILON);
        assert_eq!(inferred_facts[0].provenance, "rest 90 sec");

        let event_id = Uuid::now_v7();
        let receipts = vec![AgentWriteReceipt {
            event_id,
            event_type: "set.logged".to_string(),
            idempotency_key: "abc-123".to_string(),
            event_timestamp: Utc::now(),
        }];
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(1),
            observed_last_event_id: Some(event_id),
            detail: "ok".to_string(),
        }];
        let guard = build_claim_guard(&receipts, 1, &checks, &[], default_autonomy_policy());
        let summary = super::AgentSessionAuditSummary {
            status: "clean".to_string(),
            mismatch_detected: 0,
            mismatch_repaired: 0,
            mismatch_unresolved: 0,
            mismatch_classes: vec![],
            clarification_question: None,
        };

        let ux = build_reliability_ux(&guard, &summary, inferred_facts);
        assert_eq!(ux.state, "inferred");
        assert!(ux.assistant_phrase.contains("Inferred"));
        assert_eq!(ux.inferred_facts.len(), 1);
    }

    #[test]
    fn reliability_ux_state_unresolved_prefers_conflict_question() {
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "pending".to_string(),
            observed_projection_version: None,
            observed_last_event_id: None,
            detail: "pending".to_string(),
        }];
        let guard = build_claim_guard(&[], 1, &checks, &[], default_autonomy_policy());
        let summary = super::AgentSessionAuditSummary {
            status: "needs_clarification".to_string(),
            mismatch_detected: 1,
            mismatch_repaired: 0,
            mismatch_unresolved: 1,
            mismatch_classes: vec!["narrative_contradiction".to_string()],
            clarification_question: Some(
                "Konflikt bei Session-Feedback: Session-Anstrengung = 3 oder 8. Welcher Wert stimmt?"
                    .to_string(),
            ),
        };

        let ux = build_reliability_ux(&guard, &summary, vec![]);
        assert_eq!(ux.state, "unresolved");
        assert!(ux.assistant_phrase.contains("Unresolved"));
        assert!(
            ux.clarification_question
                .as_deref()
                .unwrap_or("")
                .contains("Welcher Wert stimmt?")
        );
    }

    #[test]
    fn save_handshake_learning_signal_verified_uses_pseudonymous_user_ref() {
        let user_id = Uuid::now_v7();
        let event_id = Uuid::now_v7();
        let receipts = vec![AgentWriteReceipt {
            event_id,
            event_type: "set.logged".to_string(),
            idempotency_key: "abc-123".to_string(),
            event_timestamp: Utc::now(),
        }];
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(5),
            observed_last_event_id: Some(event_id),
            detail: "ok".to_string(),
        }];
        let verification = super::AgentWriteVerificationSummary {
            status: "verified".to_string(),
            checked_at: Utc::now(),
            waited_ms: 10,
            write_path: "fresh_write".to_string(),
            required_checks: 1,
            verified_checks: 1,
            checks: checks.clone(),
        };
        let guard = build_claim_guard(&receipts, 1, &checks, &[], default_autonomy_policy());

        let events = build_save_handshake_learning_signal_events(
            user_id,
            1,
            &receipts,
            &verification,
            &guard,
        );

        assert_eq!(events.len(), 1);
        assert_eq!(events[0].event_type, "learning.signal.logged");
        assert_eq!(events[0].data["signal_type"], "save_handshake_verified");
        let pseudo = events[0].data["user_ref"]["pseudonymized_user_id"]
            .as_str()
            .unwrap_or("");
        assert!(pseudo.starts_with("u_"));
        assert!(!pseudo.contains(&user_id.to_string()));
    }

    #[test]
    fn save_handshake_learning_signal_pending_emits_pending_and_mismatch() {
        let user_id = Uuid::now_v7();
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "abc-123".to_string(),
            event_timestamp: Utc::now(),
        }];
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            status: "pending".to_string(),
            observed_projection_version: None,
            observed_last_event_id: None,
            detail: "pending".to_string(),
        }];
        let verification = super::AgentWriteVerificationSummary {
            status: "pending".to_string(),
            checked_at: Utc::now(),
            waited_ms: 40,
            write_path: "fresh_write".to_string(),
            required_checks: 1,
            verified_checks: 0,
            checks: checks.clone(),
        };
        let guard = build_claim_guard(&receipts, 1, &checks, &[], default_autonomy_policy());

        let events = build_save_handshake_learning_signal_events(
            user_id,
            1,
            &receipts,
            &verification,
            &guard,
        );

        assert_eq!(events.len(), 2);
        let signal_types: Vec<String> = events
            .iter()
            .filter_map(|event| {
                event
                    .data
                    .get("signal_type")
                    .and_then(|value| value.as_str())
                    .map(|value| value.to_string())
            })
            .collect();
        assert!(signal_types.iter().any(|v| v == "save_handshake_pending"));
        assert!(
            signal_types
                .iter()
                .any(|v| v == "save_claim_mismatch_attempt")
        );
    }

    // -----------------------------------------------------------------------
    // Mention-bound field extraction (regex correctness)
    // -----------------------------------------------------------------------

    #[test]
    fn parse_rest_seconds_from_bare_number() {
        // "pause 90" → 90 seconds
        assert_eq!(parse_rest_seconds_from_text("pause 90"), Some(90.0));
        assert_eq!(parse_rest_seconds_from_text("rest 120"), Some(120.0));
        assert_eq!(parse_rest_seconds_from_text("satzpause 60"), Some(60.0));
    }

    #[test]
    fn parse_rest_seconds_with_unit() {
        assert_eq!(parse_rest_seconds_from_text("rest 90 sec"), Some(90.0));
        assert_eq!(parse_rest_seconds_from_text("pause 90s"), Some(90.0));
        assert_eq!(
            parse_rest_seconds_from_text("120 seconds rest"),
            Some(120.0)
        );
    }

    #[test]
    fn parse_rest_minutes_converts_to_seconds() {
        assert_eq!(parse_rest_seconds_from_text("rest 2 min"), Some(120.0));
        assert_eq!(parse_rest_seconds_from_text("pause 3 minutes"), Some(180.0));
    }

    #[test]
    fn parse_rest_mmss_format() {
        assert_eq!(parse_rest_seconds_from_text("rest 1:30"), Some(90.0));
        assert_eq!(parse_rest_seconds_from_text("pause 2:00"), Some(120.0));
    }

    #[test]
    fn parse_rest_returns_none_for_no_mention() {
        assert_eq!(parse_rest_seconds_from_text("heavy set today"), None);
        assert_eq!(parse_rest_seconds_from_text(""), None);
    }

    #[test]
    fn parse_rir_from_various_formats() {
        assert_eq!(parse_rir_from_text("rir 2"), Some(2.0));
        assert_eq!(parse_rir_from_text("rir: 3"), Some(3.0));
        assert_eq!(parse_rir_from_text("2 rir"), Some(2.0));
        assert_eq!(parse_rir_from_text("3 reps in reserve"), Some(3.0));
    }

    #[test]
    fn parse_rir_clamps_to_range() {
        assert_eq!(parse_rir_from_text("rir 15"), Some(10.0));
    }

    #[test]
    fn parse_rir_returns_none_for_no_mention() {
        assert_eq!(parse_rir_from_text("felt easy"), None);
        assert_eq!(parse_rir_from_text(""), None);
    }

    #[test]
    fn parse_tempo_from_labeled_and_bare() {
        assert_eq!(
            parse_tempo_from_text("tempo 3-1-x-0"),
            Some("3-1-x-0".to_string())
        );
        assert_eq!(
            parse_tempo_from_text("tempo: 2-0-2-0"),
            Some("2-0-2-0".to_string())
        );
        // Bare pattern without "tempo" label
        assert_eq!(
            parse_tempo_from_text("did 3-1-x-0 today"),
            Some("3-1-x-0".to_string())
        );
    }

    #[test]
    fn parse_tempo_returns_none_for_no_mention() {
        assert_eq!(parse_tempo_from_text("heavy singles"), None);
    }

    #[test]
    fn parse_mentions_with_span_tracks_offsets() {
        let text = "3x5 squat, rest 90 sec, rir 2, tempo 3-1-x-0, warmup";

        let rest = parse_rest_with_span(text).expect("rest span");
        assert_eq!(rest.value.as_f64(), Some(90.0));
        assert_eq!(rest.span_text.to_lowercase(), "rest 90 sec");
        assert!(rest.span_end > rest.span_start);

        let rir = parse_rir_with_span(text).expect("rir span");
        assert_eq!(rir.value.as_f64(), Some(2.0));
        assert_eq!(rir.span_text.to_lowercase(), "rir 2");

        let tempo = parse_tempo_with_span(text).expect("tempo span");
        assert_eq!(tempo.value.as_str(), Some("3-1-x-0"));
        assert_eq!(tempo.span_text.to_lowercase(), "tempo 3-1-x-0");

        let set_type = parse_set_type_with_span(text).expect("set_type span");
        assert_eq!(set_type.value.as_str(), Some("warmup"));
        assert_eq!(set_type.span_text.to_lowercase(), "warmup");
    }

    #[test]
    fn normalize_set_type_maps_known_types() {
        assert_eq!(normalize_set_type("warmup"), Some("warmup".to_string()));
        assert_eq!(normalize_set_type("Warm-Up"), Some("warmup".to_string()));
        assert_eq!(
            normalize_set_type("backoff set"),
            Some("backoff".to_string())
        );
        assert_eq!(normalize_set_type("AMRAP"), Some("amrap".to_string()));
        assert_eq!(
            normalize_set_type("working set"),
            Some("working".to_string())
        );
    }

    #[test]
    fn normalize_set_type_returns_none_for_unknown() {
        assert_eq!(normalize_set_type("heavy"), None);
        assert_eq!(normalize_set_type(""), None);
    }

    #[test]
    fn extract_set_context_mentions_combined_text() {
        let mentions =
            extract_set_context_mentions_from_text("rest 90 sec, rir 2, tempo 3-1-x-0, warmup");
        assert_eq!(
            mentions.get("rest_seconds").and_then(Value::as_f64),
            Some(90.0)
        );
        assert_eq!(mentions.get("rir").and_then(Value::as_f64), Some(2.0));
        assert_eq!(
            mentions.get("tempo").and_then(Value::as_str),
            Some("3-1-x-0")
        );
        assert_eq!(
            mentions.get("set_type").and_then(Value::as_str),
            Some("warmup")
        );
    }

    #[test]
    fn extract_set_context_mentions_empty_text_returns_empty() {
        let mentions = extract_set_context_mentions_from_text("");
        assert!(mentions.is_empty());
    }

    #[test]
    fn extract_evidence_claim_drafts_contains_source_field_and_span() {
        let event = make_set_event(
            json!({
                "exercise_id": "barbell_back_squat",
                "utterance": "3x5 squat, rest 90 sec, rir 2, tempo 3-1-x-0, warmup"
            }),
            Some("session-42"),
            "idem-evidence-1",
        );

        let drafts = extract_evidence_claim_drafts(&event);
        assert!(drafts.iter().any(|claim| {
            claim.claim_type == "set_context.rest_seconds"
                && claim.value.as_f64() == Some(90.0)
                && claim.source_field == "utterance"
                && claim.span_text.to_lowercase() == "rest 90 sec"
        }));
        assert!(drafts.iter().any(|claim| {
            claim.claim_type == "set_context.rir"
                && claim.value.as_f64() == Some(2.0)
                && claim.span_text.to_lowercase() == "rir 2"
        }));
    }

    #[test]
    fn evidence_claim_events_are_deterministic_for_replay_snippet() {
        let user_id = Uuid::parse_str("00000000-0000-0000-0000-000000000123").unwrap();
        let event_id = Uuid::parse_str("00000000-0000-0000-0000-000000000456").unwrap();
        let ts = chrono::DateTime::parse_from_rfc3339("2026-02-12T10:00:00Z")
            .unwrap()
            .with_timezone(&Utc);

        let build_event = || CreateEventRequest {
            timestamp: ts,
            event_type: "set.logged".to_string(),
            data: json!({
                "exercise_id": "barbell_back_squat",
                "utterance": "3x5 squat, rest 90 sec, rir 2, tempo 3-1-x-0, warmup",
            }),
            metadata: EventMetadata {
                source: Some("agent_write_with_proof".to_string()),
                agent: Some("api".to_string()),
                device: None,
                session_id: Some("session-42".to_string()),
                idempotency_key: "idem-constant".to_string(),
            },
        };
        let receipt = AgentWriteReceipt {
            event_id,
            event_type: "set.logged".to_string(),
            idempotency_key: "idem-constant".to_string(),
            event_timestamp: ts,
        };

        let first =
            build_evidence_claim_events(user_id, &[build_event()], std::slice::from_ref(&receipt));
        let second =
            build_evidence_claim_events(user_id, &[build_event()], std::slice::from_ref(&receipt));

        assert!(!first.is_empty());
        assert_eq!(first.len(), second.len());
        for (left, right) in first.iter().zip(second.iter()) {
            assert_eq!(left.event_type, "evidence.claim.logged");
            assert_eq!(
                left.metadata.idempotency_key,
                right.metadata.idempotency_key
            );
            assert_eq!(left.data, right.data);
        }
    }

    // -----------------------------------------------------------------------
    // autonomy_policy_from_quality_health
    // -----------------------------------------------------------------------

    #[test]
    fn autonomy_policy_returns_defaults_when_no_quality_health() {
        let policy = super::autonomy_policy_from_quality_health(None);
        assert_eq!(policy.slo_status, "healthy");
        assert_eq!(policy.calibration_status, "healthy");
        assert!(!policy.throttle_active);
        assert_eq!(policy.max_scope_level, "moderate");
        assert!(policy.repair_auto_apply_enabled);
        assert!(!policy.require_confirmation_for_repairs);
    }

    #[test]
    fn autonomy_policy_extracts_degraded_state_from_quality_health() {
        let now = Utc::now();
        let projection = make_projection_response(
            "quality_health",
            "overview",
            now,
            json!({
                "autonomy_policy": {
                    "policy_version": "phase_3_integrity_slo_v1",
                    "slo_status": "degraded",
                    "calibration_status": "degraded",
                    "throttle_active": true,
                    "max_scope_level": "strict",
                    "require_confirmation_for_non_trivial_actions": true,
                    "require_confirmation_for_plan_updates": true,
                    "require_confirmation_for_repairs": true,
                    "repair_auto_apply_enabled": false,
                    "reason": "SLO breach: unresolved rate > monitor threshold"
                }
            }),
        );

        let policy = super::autonomy_policy_from_quality_health(Some(&projection));
        assert_eq!(policy.slo_status, "degraded");
        assert_eq!(policy.calibration_status, "degraded");
        assert!(policy.throttle_active);
        assert_eq!(policy.max_scope_level, "strict");
        assert!(!policy.repair_auto_apply_enabled);
        assert!(policy.require_confirmation_for_non_trivial_actions);
        assert!(policy.require_confirmation_for_plan_updates);
        assert!(policy.require_confirmation_for_repairs);
    }

    #[test]
    fn autonomy_policy_falls_back_to_defaults_for_missing_fields() {
        let now = Utc::now();
        let projection = make_projection_response(
            "quality_health",
            "overview",
            now,
            json!({
                "autonomy_policy": {
                    "slo_status": "monitor"
                }
            }),
        );

        let policy = super::autonomy_policy_from_quality_health(Some(&projection));
        assert_eq!(policy.slo_status, "monitor");
        assert_eq!(policy.calibration_status, "healthy");
        // All other fields should use defaults
        assert!(!policy.throttle_active);
        assert_eq!(policy.max_scope_level, "moderate");
        assert!(policy.repair_auto_apply_enabled);
    }

    #[test]
    fn autonomy_policy_uses_defaults_when_projection_has_no_policy_key() {
        let now = Utc::now();
        let projection = make_projection_response(
            "quality_health",
            "overview",
            now,
            json!({"score": 0.95, "issues_open": 1}),
        );

        let policy = super::autonomy_policy_from_quality_health(Some(&projection));
        assert_eq!(policy.slo_status, "healthy");
        assert_eq!(policy.calibration_status, "healthy");
        assert!(!policy.throttle_active);
    }

    #[test]
    fn session_audit_auto_repair_is_blocked_when_calibration_is_degraded() {
        let mut policy = super::default_autonomy_policy();
        policy.calibration_status = "degraded".to_string();
        assert!(!super::session_audit_auto_repair_allowed(&policy));
    }

    #[test]
    fn capabilities_manifest_exposes_agent_contract_preferences() {
        let manifest = build_agent_capabilities();
        assert_eq!(manifest.schema_version, "agent_capabilities.v1");
        assert_eq!(manifest.preferred_read_endpoint, "/v1/agent/context");
        assert_eq!(
            manifest.preferred_write_endpoint,
            "/v1/agent/write-with-proof"
        );
        assert!(manifest.required_verification_contract.requires_receipts);
        assert!(
            manifest
                .required_verification_contract
                .requires_read_after_write
        );
        assert!(!manifest.min_cli_version.trim().is_empty());
    }

    #[test]
    fn capabilities_manifest_contains_fallbacks_and_upgrade_policy() {
        let manifest = build_agent_capabilities();
        assert!(manifest.supported_fallbacks.iter().any(|fallback| {
            fallback.endpoint == "/v1/events"
                && fallback.compatibility_status == "supported_with_upgrade_signal"
        }));
        assert!(
            manifest
                .upgrade_policy
                .phases
                .iter()
                .any(|phase| phase.compatibility_status == "deprecated")
        );
        assert_eq!(
            manifest.upgrade_policy.upgrade_signal_header,
            "x-kura-upgrade-signal"
        );
    }

    #[test]
    fn system_config_redaction_keeps_only_public_contract_fields() {
        let redacted = super::redact_system_config_data_for_agent(json!({
            "dimensions": {"training_timeline": {"description": "ok"}},
            "event_conventions": [{"event_type": "set.logged"}],
            "projection_schemas": {"user_profile": {"required_fields": ["user"]}},
            "conventions": {
                "exercise_normalization": {"rules": ["rule"]},
                "training_core_fields_v1": {"rules": ["rule"]},
                "evidence_layer_v1": {"event_type": "evidence.claim.logged"},
                "open_observation_v1": {"event_type": "observation.logged"},
                "ingestion_locale_v1": {"rules": ["normalize decimals"]},
                "load_context_v1": {"event_type": "set.logged"},
                "session_feedback_certainty_v1": {"event_type": "session.completed"},
                "schema_capability_gate_v1": {"rules": ["capability checks"]},
                "learning_clustering_v1": {"rules": ["internal"]},
                "shadow_evaluation_gate_v1": {"rules": ["internal"]},
                "unexpected_convention": {"rules": ["unknown"]}
            },
            "interview_guide": {"philosophy": ["internal strategy"]},
            "agent_behavior": {"operational": {"security_tiering": {}}},
            "unexpected_root": {"anything": true}
        }));

        let root = redacted
            .as_object()
            .expect("redacted system config should be an object");
        assert!(root.contains_key("dimensions"));
        assert!(root.contains_key("event_conventions"));
        assert!(root.contains_key("projection_schemas"));
        assert!(root.contains_key("conventions"));
        assert!(!root.contains_key("interview_guide"));
        assert!(!root.contains_key("agent_behavior"));
        assert!(!root.contains_key("unexpected_root"));

        let conventions = root
            .get("conventions")
            .and_then(Value::as_object)
            .expect("conventions should remain an object");
        assert!(conventions.contains_key("exercise_normalization"));
        assert!(conventions.contains_key("training_core_fields_v1"));
        assert!(conventions.contains_key("evidence_layer_v1"));
        assert!(conventions.contains_key("open_observation_v1"));
        assert!(conventions.contains_key("ingestion_locale_v1"));
        assert!(conventions.contains_key("load_context_v1"));
        assert!(conventions.contains_key("session_feedback_certainty_v1"));
        assert!(conventions.contains_key("schema_capability_gate_v1"));
        assert!(!conventions.contains_key("learning_clustering_v1"));
        assert!(!conventions.contains_key("shadow_evaluation_gate_v1"));
        assert!(!conventions.contains_key("unexpected_convention"));
    }

    #[test]
    fn system_config_redaction_returns_empty_object_for_non_object_input() {
        let redacted = super::redact_system_config_data_for_agent(json!(["not", "an", "object"]));
        assert_eq!(redacted, json!({}));
    }

    #[test]
    fn agent_context_system_contract_is_versioned_and_deny_by_default() {
        let contract = super::build_agent_context_system_contract();
        assert_eq!(contract.profile, "redacted_v1");
        assert_eq!(contract.schema_version, "agent_context.system.v1");
        assert_eq!(contract.default_unknown_field_action, "deny");
        assert!(
            contract
                .redacted_field_classes
                .iter()
                .any(|class| class == "system.internal_strategy")
        );
        assert!(
            contract
                .redacted_field_classes
                .iter()
                .any(|class| class == "system.conventions.internal_operations")
        );
    }
}
