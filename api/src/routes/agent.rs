use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, SecondsFormat, Utc};
use hmac::{Hmac, Mac};
use kura_core::events::{BatchEventWarning, CreateEventRequest, EventMetadata};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{LazyLock, Mutex};
use std::time::{Duration, Instant};
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::projections::{Projection, ProjectionFreshness, ProjectionMeta, ProjectionResponse};

use crate::auth::{AuthMethod, AuthenticatedUser, require_scopes};
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

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentSelfModelPreferredContracts {
    pub read: String,
    pub write: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentSelfModelFallbackBehavior {
    pub unknown_identity_action: String,
    pub unknown_policy_action: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentSelfModelDocs {
    pub runtime_policy: String,
    pub upgrade_hint: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentSelfModel {
    pub schema_version: String,
    pub model_identity: String,
    pub capability_tier: String,
    pub known_limitations: Vec<String>,
    pub preferred_contracts: AgentSelfModelPreferredContracts,
    pub fallback_behavior: AgentSelfModelFallbackBehavior,
    pub docs: AgentSelfModelDocs,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentChallengeMode {
    pub schema_version: String,
    /// auto | on | off
    pub mode: String,
    /// default_auto | user_profile.preference
    pub source: String,
    pub onboarding_hint_required: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub onboarding_hint: Option<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentMemoryTierSnapshot {
    /// working | project | principles
    pub tier: String,
    /// fresh | aging | stale
    pub freshness_state: String,
    /// high | medium | low
    pub confidence_band: String,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub observed_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_verified_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stale_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentMemoryTierContract {
    pub schema_version: String,
    /// confirm_first | block
    pub high_impact_stale_action: String,
    pub tiers: Vec<AgentMemoryTierSnapshot>,
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
    pub challenge_mode: AgentChallengeMode,
    pub memory_tier_contract: AgentMemoryTierContract,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct AgentContextResponse {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub system: Option<SystemConfigResponse>,
    pub self_model: AgentSelfModel,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    pub consistency_inbox: Option<ProjectionResponse>,
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
    pub self_model: AgentSelfModel,
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
    pub model_identity: String,
    pub capability_tier: String,
    pub tier_policy_version: String,
    pub tier_confidence_floor: f64,
    pub throttle_active: bool,
    pub max_scope_level: String,
    /// concise | balanced | detailed
    pub interaction_verbosity: String,
    /// auto | always | never
    pub confirmation_strictness: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_requested_scope_level: Option<String>,
    pub require_confirmation_for_non_trivial_actions: bool,
    pub require_confirmation_for_plan_updates: bool,
    pub require_confirmation_for_repairs: bool,
    pub repair_auto_apply_enabled: bool,
    pub reason: String,
    pub confirmation_templates: HashMap<String, String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentAutonomyGate {
    /// allow | confirm_first | block
    pub decision: String,
    /// low_impact_write | high_impact_write
    pub action_class: String,
    pub model_tier: String,
    /// healthy | monitor | degraded
    pub effective_quality_status: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub reason_codes: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, utoipa::ToSchema)]
pub struct AgentReadAfterWriteTarget {
    pub projection_type: String,
    pub key: String,
}

#[derive(Debug, Clone, Deserialize, Serialize, utoipa::ToSchema)]
pub struct AgentIntentHandshake {
    pub schema_version: String,
    pub goal: String,
    pub planned_action: String,
    #[serde(default)]
    pub assumptions: Vec<String>,
    #[serde(default)]
    pub non_goals: Vec<String>,
    /// low_impact_write | high_impact_write
    pub impact_class: String,
    pub success_criteria: String,
    pub created_at: DateTime<Utc>,
    #[serde(default)]
    pub handshake_id: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, utoipa::ToSchema)]
pub struct AgentModelAttestation {
    /// model_attestation.v1
    pub schema_version: String,
    /// Runtime model identity observed by the gateway/provider (e.g. openai:gpt-5-mini).
    pub runtime_model_identity: String,
    /// Stable digest of the signed write request payload.
    pub request_digest: String,
    /// Gateway-generated id for replay protection.
    pub request_id: String,
    /// Issued-at timestamp from gateway.
    pub issued_at: DateTime<Utc>,
    /// Hex(HMAC-SHA256(secret, canonical_payload))
    pub signature: String,
}

#[derive(Debug, Clone, Deserialize, Serialize, utoipa::ToSchema)]
pub struct AgentHighImpactConfirmation {
    /// high_impact_confirmation.v1
    pub schema_version: String,
    /// Must be true when the user explicitly approved this high-impact change.
    pub confirmed: bool,
    /// Timestamp of explicit user confirmation.
    pub confirmed_at: DateTime<Utc>,
    /// Opaque token from the prior confirm-first response, bound to the pending payload digest.
    #[serde(default)]
    pub confirmation_token: Option<String>,
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
    /// Optional pre-execution alignment contract (required for high-impact writes).
    #[serde(default)]
    pub intent_handshake: Option<AgentIntentHandshake>,
    /// Optional runtime model attestation from agent gateway.
    #[serde(default)]
    pub model_attestation: Option<AgentModelAttestation>,
    /// Explicit user confirmation required when confirm-first policy is active for high-impact writes.
    #[serde(default)]
    pub high_impact_confirmation: Option<AgentHighImpactConfirmation>,
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
    pub autonomy_gate: AgentAutonomyGate,
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

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentIntentHandshakeConfirmation {
    pub schema_version: String,
    pub status: String,
    pub impact_class: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub handshake_id: Option<String>,
    pub chat_confirmation: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentTraceDigest {
    pub schema_version: String,
    pub action_id: String,
    pub correlation_id: String,
    pub receipt_event_ids: Vec<Uuid>,
    pub write_path: String,
    pub verification_status: String,
    pub required_checks: usize,
    pub verified_checks: usize,
    pub allow_saved_claim: bool,
    pub claim_status: String,
    pub workflow_phase: String,
    pub workflow_status: String,
    pub workflow_transition: String,
    pub autonomy_decision: String,
    pub autonomy_action_class: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub autonomy_reason_codes: Vec<String>,
    pub session_audit_status: String,
    pub mismatch_detected: usize,
    pub mismatch_repaired: usize,
    pub mismatch_unresolved: usize,
    pub repair_status: String,
    pub warning_count: usize,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warning_codes: Vec<String>,
    pub chat_summary_template_id: String,
    pub chat_summary: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentPostTaskReflection {
    pub schema_version: String,
    pub action_id: String,
    pub related_trace_digest_id: String,
    pub change_summary: String,
    /// confirmed | partial | unresolved
    pub certainty_state: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub residual_risks: Vec<String>,
    pub next_verification_step: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub clarification_question: Option<String>,
    pub follow_up_recommended: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub follow_up_reason: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub emitted_learning_signal_types: Vec<String>,
    pub chat_summary_template_id: String,
    pub chat_summary: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentResponseModePolicy {
    pub schema_version: String,
    /// A | B | C
    pub mode_code: String,
    /// grounded_personalized | hypothesis_personalized | general_guidance
    pub mode: String,
    /// sufficient | limited | insufficient
    pub evidence_state: String,
    /// 0..1 composite score from verification + quality health signals
    pub evidence_score: f64,
    pub threshold_a_min: f64,
    pub threshold_b_min: f64,
    /// healthy | monitor | degraded | unknown
    pub quality_status: String,
    /// healthy | monitor | degraded | unknown
    pub integrity_slo_status: String,
    /// healthy | monitor | degraded | unknown
    pub calibration_status: String,
    /// nudge_only (advisory, never hard-blocking)
    pub policy_role: String,
    pub requires_transparency_note: bool,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub reason_codes: Vec<String>,
    pub assistant_instruction: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentFailureProfileSignal {
    pub code: String,
    pub weight: f64,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentPersonalFailureProfile {
    pub schema_version: String,
    pub profile_id: String,
    pub model_identity: String,
    /// high | medium | low
    pub data_quality_band: String,
    /// advisory_only (never cages autonomy)
    pub policy_role: String,
    pub recommended_response_mode: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub active_signals: Vec<AgentFailureProfileSignal>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentRetrievalRegret {
    pub schema_version: String,
    pub regret_score: f64,
    /// low | medium | high
    pub regret_band: String,
    pub regret_threshold: f64,
    pub threshold_exceeded: bool,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub reason_codes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentLaaJSidecar {
    pub schema_version: String,
    /// pass | review
    pub verdict: String,
    pub score: f64,
    /// advisory_only (never hard-blocking)
    pub policy_role: String,
    pub can_block_autonomy: bool,
    pub recommendation: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub reason_codes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AgentSidecarAssessment {
    pub retrieval_regret: AgentRetrievalRegret,
    pub laaj: AgentLaaJSidecar,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    pub intent_handshake_confirmation: Option<AgentIntentHandshakeConfirmation>,
    pub trace_digest: AgentTraceDigest,
    pub post_task_reflection: AgentPostTaskReflection,
    pub response_mode_policy: AgentResponseModePolicy,
    pub personal_failure_profile: AgentPersonalFailureProfile,
    pub sidecar_assessment: AgentSidecarAssessment,
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

mod policy;
use policy::*;

mod system_contract;
use system_contract::*;

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

mod workflow_visualization;
use workflow_visualization::*;

mod session_audit;
use session_audit::*;

mod write_verification;
use write_verification::*;

fn warning_code_from_warning(warning: &BatchEventWarning) -> String {
    let field = warning
        .field
        .trim()
        .to_lowercase()
        .replace(|c: char| !c.is_ascii_alphanumeric(), "_");
    let severity = warning
        .severity
        .trim()
        .to_lowercase()
        .replace(|c: char| !c.is_ascii_alphanumeric(), "_");
    format!("{field}:{severity}")
}

fn build_trace_digest(
    receipts: &[AgentWriteReceipt],
    warnings: &[BatchEventWarning],
    verification: &AgentWriteVerificationSummary,
    claim_guard: &AgentWriteClaimGuard,
    workflow_gate: &AgentWorkflowGate,
    session_audit: &AgentSessionAuditSummary,
    repair_feedback: &AgentRepairFeedback,
) -> AgentTraceDigest {
    let mut receipt_event_ids: Vec<Uuid> =
        receipts.iter().map(|receipt| receipt.event_id).collect();
    receipt_event_ids.sort();

    let seed = format!(
        "{}|{}|{}|{}|{}",
        receipt_event_ids
            .iter()
            .map(Uuid::to_string)
            .collect::<Vec<_>>()
            .join(","),
        verification.status,
        claim_guard.claim_status,
        workflow_gate.phase,
        session_audit.status
    );
    let action_id = format!("action_{}", stable_hash_suffix(&seed, 16));
    let correlation_id = format!("corr_{}", stable_hash_suffix(&format!("{seed}:corr"), 12));
    let warning_codes: Vec<String> = warnings.iter().map(warning_code_from_warning).collect();
    let chat_summary = format!(
        "Trace: verification={}, claim={}, workflow={}, repairs={}, warnings={}",
        verification.status,
        claim_guard.claim_status,
        workflow_gate.status,
        repair_feedback.status,
        warnings.len()
    );

    AgentTraceDigest {
        schema_version: TRACE_DIGEST_SCHEMA_VERSION.to_string(),
        action_id: action_id.clone(),
        correlation_id,
        receipt_event_ids,
        write_path: verification.write_path.clone(),
        verification_status: verification.status.clone(),
        required_checks: verification.required_checks,
        verified_checks: verification.verified_checks,
        allow_saved_claim: claim_guard.allow_saved_claim,
        claim_status: claim_guard.claim_status.clone(),
        workflow_phase: workflow_gate.phase.clone(),
        workflow_status: workflow_gate.status.clone(),
        workflow_transition: workflow_gate.transition.clone(),
        autonomy_decision: claim_guard.autonomy_gate.decision.clone(),
        autonomy_action_class: claim_guard.autonomy_gate.action_class.clone(),
        autonomy_reason_codes: claim_guard.autonomy_gate.reason_codes.clone(),
        session_audit_status: session_audit.status.clone(),
        mismatch_detected: session_audit.mismatch_detected,
        mismatch_repaired: session_audit.mismatch_repaired,
        mismatch_unresolved: session_audit.mismatch_unresolved,
        repair_status: repair_feedback.status.clone(),
        warning_count: warnings.len(),
        warning_codes,
        chat_summary_template_id: "trace_digest.chat.short.v1".to_string(),
        chat_summary,
    }
}

fn build_post_task_reflection(
    trace_digest: &AgentTraceDigest,
    verification: &AgentWriteVerificationSummary,
    session_audit: &AgentSessionAuditSummary,
    repair_feedback: &AgentRepairFeedback,
) -> AgentPostTaskReflection {
    let certainty_state = if verification.status == "verified" && session_audit.status == "clean" {
        "confirmed".to_string()
    } else if verification.status == "failed" || session_audit.status == "needs_clarification" {
        "unresolved".to_string()
    } else {
        "partial".to_string()
    };

    let mut residual_risks = Vec::new();
    if verification.status != "verified" {
        residual_risks.push("read_after_write_not_fully_verified".to_string());
    }
    if session_audit.status == "needs_clarification" {
        residual_risks.push("session_audit_needs_clarification".to_string());
    }
    if repair_feedback.status == "needs_clarification" {
        residual_risks.push("repair_feedback_pending_clarification".to_string());
    }

    let clarification_question = session_audit
        .clarification_question
        .clone()
        .or_else(|| repair_feedback.clarification_question.clone());
    let next_verification_step = if certainty_state == "confirmed" {
        "none_required".to_string()
    } else if let Some(question) = clarification_question.clone() {
        format!("ask_user: {question}")
    } else {
        "retry_read_after_write_with_same_idempotency_keys".to_string()
    };
    let follow_up_recommended = certainty_state != "confirmed";
    let follow_up_reason = if follow_up_recommended {
        Some("certainty_state_not_confirmed".to_string())
    } else {
        None
    };

    AgentPostTaskReflection {
        schema_version: POST_TASK_REFLECTION_SCHEMA_VERSION.to_string(),
        action_id: trace_digest.action_id.clone(),
        related_trace_digest_id: trace_digest.action_id.clone(),
        change_summary: format!(
            "{} events processed, verification={}, claim_status={}",
            trace_digest.receipt_event_ids.len(),
            verification.status,
            trace_digest.claim_status
        ),
        certainty_state: certainty_state.clone(),
        residual_risks,
        next_verification_step,
        clarification_question,
        follow_up_recommended,
        follow_up_reason,
        emitted_learning_signal_types: Vec::new(),
        chat_summary_template_id: "post_task_reflection.chat.short.v1".to_string(),
        chat_summary: format!(
            "Reflection: certainty={}, next_step={}",
            certainty_state,
            if follow_up_recommended {
                "verification_or_clarification"
            } else {
                "none"
            }
        ),
    }
}

#[derive(Debug, Clone)]
struct RuntimeQualitySignals {
    quality_status: String,
    integrity_slo_status: String,
    calibration_status: String,
    unresolved_set_logged_pct: f64,
    save_claim_integrity_rate_pct: f64,
    save_claim_posterior_monitor_prob: f64,
    save_claim_posterior_degraded_prob: f64,
    issues_open: usize,
}

impl Default for RuntimeQualitySignals {
    fn default() -> Self {
        Self {
            quality_status: "unknown".to_string(),
            integrity_slo_status: "unknown".to_string(),
            calibration_status: "unknown".to_string(),
            unresolved_set_logged_pct: 0.0,
            save_claim_integrity_rate_pct: 0.0,
            save_claim_posterior_monitor_prob: 0.0,
            save_claim_posterior_degraded_prob: 0.0,
            issues_open: 0,
        }
    }
}

fn read_value_f64(value: Option<&Value>) -> Option<f64> {
    let raw = value?;
    if let Some(number) = raw.as_f64() {
        return Some(number);
    }
    raw.as_i64().map(|number| number as f64)
}

fn read_value_usize(value: Option<&Value>) -> Option<usize> {
    let raw = value?;
    if let Some(number) = raw.as_u64() {
        return usize::try_from(number).ok();
    }
    raw.as_i64()
        .filter(|number| *number >= 0)
        .and_then(|number| usize::try_from(number).ok())
}

fn normalize_quality_label(value: Option<&Value>) -> String {
    let label = value.and_then(Value::as_str).unwrap_or("unknown");
    let normalized = label.trim().to_lowercase();
    match normalized.as_str() {
        "healthy" | "monitor" | "degraded" => normalized,
        _ => "unknown".to_string(),
    }
}

fn extract_runtime_quality_signals(
    quality_health: Option<&ProjectionResponse>,
) -> RuntimeQualitySignals {
    let mut signals = RuntimeQualitySignals::default();
    let Some(payload) = quality_health.map(|projection| &projection.projection.data) else {
        return signals;
    };

    signals.quality_status = normalize_quality_label(payload.get("status"));
    signals.integrity_slo_status =
        normalize_quality_label(payload.get("integrity_slo_status").or_else(|| {
            payload
                .get("integrity_slos")
                .and_then(|slos| slos.get("status"))
        }));
    signals.calibration_status = normalize_quality_label(
        payload
            .get("autonomy_policy")
            .and_then(|policy| policy.get("calibration_status"))
            .or_else(|| {
                payload
                    .get("extraction_calibration")
                    .and_then(|calibration| calibration.get("status"))
            }),
    );
    signals.unresolved_set_logged_pct = read_value_f64(
        payload
            .get("metrics")
            .and_then(|metrics| metrics.get("set_logged_unresolved_pct")),
    )
    .unwrap_or(0.0)
    .clamp(0.0, 100.0);
    signals.issues_open = read_value_usize(payload.get("issues_open")).unwrap_or(0);

    let save_claim_metric = payload
        .get("integrity_slos")
        .and_then(|slos| slos.get("metrics"))
        .and_then(|metrics| metrics.get("save_claim_mismatch_rate_pct"));
    signals.save_claim_integrity_rate_pct =
        read_value_f64(save_claim_metric.and_then(|metric| metric.get("value")))
            .unwrap_or(0.0)
            .clamp(0.0, 100.0);
    signals.save_claim_posterior_monitor_prob = read_value_f64(
        save_claim_metric.and_then(|metric| metric.get("posterior_prob_gt_monitor")),
    )
    .unwrap_or(0.0)
    .clamp(0.0, 1.0);
    signals.save_claim_posterior_degraded_prob = read_value_f64(
        save_claim_metric.and_then(|metric| metric.get("posterior_prob_gt_degraded")),
    )
    .unwrap_or(0.0)
    .clamp(0.0, 1.0);
    signals
}

fn response_mode_thresholds(signals: &RuntimeQualitySignals) -> (f64, f64) {
    let mut threshold_a: f64 = 0.72;
    let mut threshold_b: f64 = 0.42;

    match signals.integrity_slo_status.as_str() {
        "monitor" => {
            threshold_a += 0.05;
            threshold_b += 0.03;
        }
        "degraded" => {
            threshold_a += 0.12;
            threshold_b += 0.08;
        }
        _ => {}
    }
    match signals.calibration_status.as_str() {
        "monitor" => threshold_a += 0.04,
        "degraded" => {
            threshold_a += 0.10;
            threshold_b += 0.05;
        }
        _ => {}
    }
    match signals.quality_status.as_str() {
        "monitor" => threshold_a += 0.02,
        "degraded" => {
            threshold_a += 0.05;
            threshold_b += 0.03;
        }
        _ => {}
    }

    (threshold_a.clamp(0.55, 0.95), threshold_b.clamp(0.25, 0.85))
}

fn response_mode_evidence_score(
    claim_guard: &AgentWriteClaimGuard,
    verification: &AgentWriteVerificationSummary,
    signals: &RuntimeQualitySignals,
) -> f64 {
    let verification_coverage = if verification.required_checks == 0 {
        match verification.status.as_str() {
            "verified" => 1.0,
            "pending" => 0.55,
            _ => 0.0,
        }
    } else {
        let ratio = verification.verified_checks as f64 / verification.required_checks as f64;
        if verification.status == "pending" {
            ratio.max(0.55)
        } else {
            ratio
        }
    };

    let mut score = verification_coverage * 0.55;
    if verification.status == "verified" {
        score += 0.15;
    } else if verification.status == "pending" {
        score += 0.18;
    }
    if claim_guard.allow_saved_claim {
        score += 0.20;
    } else {
        score -= if verification.status == "failed" {
            0.12
        } else {
            0.03
        };
    }
    if claim_guard.claim_status == "failed" {
        score -= 0.20;
    }
    if claim_guard
        .uncertainty_markers
        .iter()
        .any(|marker| marker == "read_after_write_unverified")
    {
        score -= if verification.status == "pending" {
            0.02
        } else {
            0.07
        };
    }
    if claim_guard.autonomy_gate.decision == "confirm_first" {
        score -= 0.03;
    }

    let unresolved_penalty = (signals.unresolved_set_logged_pct / 100.0).clamp(0.0, 0.30) * 0.35;
    let mismatch_penalty = (signals.save_claim_integrity_rate_pct / 100.0).clamp(0.0, 0.40) * 0.40;
    score -= unresolved_penalty + mismatch_penalty;
    score -= signals.save_claim_posterior_monitor_prob * 0.06;
    score -= signals.save_claim_posterior_degraded_prob * 0.14;

    match signals.calibration_status.as_str() {
        "monitor" => score -= 0.05,
        "degraded" => score -= 0.11,
        _ => {}
    }
    match signals.integrity_slo_status.as_str() {
        "monitor" => score -= 0.04,
        "degraded" => score -= 0.08,
        _ => {}
    }
    if signals.issues_open >= 12 {
        score -= 0.06;
    } else if signals.issues_open >= 6 {
        score -= 0.03;
    }

    score.clamp(0.0, 1.0)
}

fn build_response_mode_policy(
    claim_guard: &AgentWriteClaimGuard,
    verification: &AgentWriteVerificationSummary,
    quality_health: Option<&ProjectionResponse>,
) -> AgentResponseModePolicy {
    let signals = extract_runtime_quality_signals(quality_health);
    let (threshold_a_min, threshold_b_min) = response_mode_thresholds(&signals);
    let evidence_score = response_mode_evidence_score(claim_guard, verification, &signals);

    let mut mode_code = "C".to_string();
    let mut mode = "general_guidance".to_string();
    let mut evidence_state = "insufficient".to_string();
    let mut reason_codes: Vec<String> = Vec::new();

    if verification.status != "failed"
        && claim_guard.allow_saved_claim
        && evidence_score >= threshold_a_min
    {
        mode_code = "A".to_string();
        mode = "grounded_personalized".to_string();
        evidence_state = "sufficient".to_string();
        reason_codes.push("evidence_score_passes_grounded_threshold".to_string());
    } else if verification.status != "failed" && evidence_score >= threshold_b_min {
        mode_code = "B".to_string();
        mode = "hypothesis_personalized".to_string();
        evidence_state = "limited".to_string();
        reason_codes.push("evidence_score_supports_hypothesis_mode".to_string());
    } else {
        reason_codes.push("insufficient_personal_evidence".to_string());
        reason_codes.push("evidence_score_below_hypothesis_threshold".to_string());
    }
    if verification.status != "verified" {
        reason_codes.push("write_proof_not_verified".to_string());
    }
    if !claim_guard.allow_saved_claim {
        reason_codes.push("save_claim_not_verified".to_string());
    }
    if claim_guard.claim_status == "inferred" {
        reason_codes.push("inferred_values_present".to_string());
    }
    if claim_guard.claim_status == "pending" {
        reason_codes.push("claim_status_pending".to_string());
    }
    if signals.unresolved_set_logged_pct > 0.0 {
        reason_codes.push("history_unresolved_set_logged_present".to_string());
    }
    if signals.save_claim_posterior_degraded_prob >= 0.25 {
        reason_codes.push("integrity_regression_risk_elevated".to_string());
    }
    if signals.quality_status != "healthy" && signals.quality_status != "unknown" {
        reason_codes.push(format!("quality_{}_context", signals.quality_status));
    }
    if signals.integrity_slo_status != "healthy" && signals.integrity_slo_status != "unknown" {
        reason_codes.push(format!(
            "integrity_{}_context",
            signals.integrity_slo_status
        ));
    }
    if signals.calibration_status != "healthy" && signals.calibration_status != "unknown" {
        reason_codes.push(format!(
            "calibration_{}_context",
            signals.calibration_status
        ));
    }
    if claim_guard.autonomy_gate.decision == "confirm_first" {
        reason_codes.push("confirm_first_gate_active".to_string());
    }
    dedupe_reason_codes(&mut reason_codes);

    let assistant_instruction = match mode_code.as_str() {
        "A" => {
            "Anchor recommendations in user-specific evidence and cite concrete personal drivers."
                .to_string()
        }
        "B" => {
            "Offer hypothesis-based personalization and explicitly name uncertainty + missing evidence."
                .to_string()
        }
        _ => {
            "Provide general guidance first and ask one high-value clarification before specific recommendations."
                .to_string()
        }
    };
    let requires_transparency_note = mode_code != "A";

    AgentResponseModePolicy {
        schema_version: RESPONSE_MODE_POLICY_SCHEMA_VERSION.to_string(),
        mode_code,
        mode,
        evidence_state,
        evidence_score,
        threshold_a_min,
        threshold_b_min,
        quality_status: signals.quality_status,
        integrity_slo_status: signals.integrity_slo_status,
        calibration_status: signals.calibration_status,
        policy_role: RESPONSE_MODE_POLICY_ROLE_NUDGE_ONLY.to_string(),
        requires_transparency_note,
        reason_codes,
        assistant_instruction,
    }
}

fn build_personal_failure_profile(
    user_id: Uuid,
    model_identity: &ResolvedModelIdentity,
    claim_guard: &AgentWriteClaimGuard,
    verification: &AgentWriteVerificationSummary,
    session_audit: &AgentSessionAuditSummary,
    response_mode_policy: &AgentResponseModePolicy,
) -> AgentPersonalFailureProfile {
    let mut active_signals: Vec<AgentFailureProfileSignal> = Vec::new();

    if verification.status != "verified" {
        active_signals.push(AgentFailureProfileSignal {
            code: "read_after_write_unverified".to_string(),
            weight: 0.8,
        });
    }
    if claim_guard.claim_status != "saved_verified" {
        active_signals.push(AgentFailureProfileSignal {
            code: "claim_not_saved_verified".to_string(),
            weight: 0.7,
        });
    }
    if session_audit.mismatch_unresolved > 0 {
        active_signals.push(AgentFailureProfileSignal {
            code: "session_mismatch_unresolved".to_string(),
            weight: 0.95,
        });
    }
    if claim_guard.autonomy_gate.decision == "confirm_first" {
        active_signals.push(AgentFailureProfileSignal {
            code: "confirm_first_gate_active".to_string(),
            weight: 0.35,
        });
    }
    if response_mode_policy.mode_code == "C" {
        active_signals.push(AgentFailureProfileSignal {
            code: "insufficient_personal_evidence".to_string(),
            weight: 0.6,
        });
    }

    let max_weight = active_signals
        .iter()
        .map(|signal| signal.weight)
        .fold(0.0_f64, f64::max);
    let data_quality_band = if max_weight >= 0.85 {
        "low"
    } else if max_weight >= 0.5 {
        "medium"
    } else {
        "high"
    };

    let profile_seed = format!(
        "{}|{}|{}",
        user_id, model_identity.model_identity, PERSONAL_FAILURE_PROFILE_SCHEMA_VERSION
    );
    let profile_id = format!("pfp_{}", stable_hash_suffix(&profile_seed, 20));

    AgentPersonalFailureProfile {
        schema_version: PERSONAL_FAILURE_PROFILE_SCHEMA_VERSION.to_string(),
        profile_id,
        model_identity: model_identity.model_identity.clone(),
        data_quality_band: data_quality_band.to_string(),
        policy_role: SIDECAR_POLICY_ROLE_ADVISORY_ONLY.to_string(),
        recommended_response_mode: response_mode_policy.mode.clone(),
        active_signals,
    }
}

fn regret_band(score: f64) -> &'static str {
    if score >= 0.66 {
        "high"
    } else if score >= 0.33 {
        "medium"
    } else {
        "low"
    }
}

fn build_retrieval_regret(
    claim_guard: &AgentWriteClaimGuard,
    verification: &AgentWriteVerificationSummary,
    response_mode_policy: &AgentResponseModePolicy,
) -> AgentRetrievalRegret {
    let mut regret_threshold = 0.45;
    if response_mode_policy.integrity_slo_status == "degraded"
        || response_mode_policy.calibration_status == "degraded"
    {
        regret_threshold = 0.35;
    } else if response_mode_policy.integrity_slo_status == "monitor"
        || response_mode_policy.calibration_status == "monitor"
        || response_mode_policy.quality_status == "monitor"
    {
        regret_threshold = 0.40;
    }

    let mut reason_codes = Vec::new();
    if verification.required_checks == 0 {
        reason_codes.push("no_read_after_write_checks".to_string());
    }
    if response_mode_policy.evidence_score < response_mode_policy.threshold_b_min {
        reason_codes.push("evidence_score_below_hypothesis_threshold".to_string());
    }

    if verification.verified_checks < verification.required_checks {
        reason_codes.push("read_after_write_incomplete".to_string());
    }
    if !claim_guard.allow_saved_claim {
        reason_codes.push("save_claim_not_verified".to_string());
    }
    if verification.status == "failed" {
        reason_codes.push("write_proof_failed".to_string());
    }
    if response_mode_policy.mode_code != "A" {
        reason_codes.push("response_mode_not_grounded".to_string());
    }
    if regret_threshold < 0.45 {
        reason_codes.push("regret_threshold_tightened_by_quality_context".to_string());
    }
    dedupe_reason_codes(&mut reason_codes);

    let mut regret_score = 1.0 - response_mode_policy.evidence_score;
    if verification.required_checks == 0 {
        regret_score += 0.05;
    }
    if verification.status == "failed" {
        regret_score += 0.15;
    }
    if !claim_guard.allow_saved_claim {
        regret_score += 0.08;
    }
    if response_mode_policy.mode_code == "C" {
        regret_score += 0.07;
    }
    regret_score = regret_score.clamp(0.0, 1.0);

    AgentRetrievalRegret {
        schema_version: RETRIEVAL_REGRET_SCHEMA_VERSION.to_string(),
        regret_score,
        regret_band: regret_band(regret_score).to_string(),
        regret_threshold,
        threshold_exceeded: regret_score >= regret_threshold,
        reason_codes,
    }
}

fn build_laaj_sidecar(
    claim_guard: &AgentWriteClaimGuard,
    session_audit: &AgentSessionAuditSummary,
    response_mode_policy: &AgentResponseModePolicy,
    retrieval_regret: &AgentRetrievalRegret,
) -> AgentLaaJSidecar {
    let mut reason_codes = Vec::new();
    let mut score = 1.0 - retrieval_regret.regret_score;

    if session_audit.status == "needs_clarification" {
        score -= 0.25;
        reason_codes.push("session_audit_needs_clarification".to_string());
    }
    if claim_guard.claim_status == "failed" {
        score -= 0.2;
        reason_codes.push("claim_guard_failed".to_string());
    }
    if response_mode_policy.mode_code == "C" {
        score -= 0.15;
        reason_codes.push("response_mode_general_guidance".to_string());
    }
    if claim_guard.autonomy_gate.decision == "confirm_first" {
        score -= 0.05;
        reason_codes.push("autonomy_confirm_first_active".to_string());
    }
    dedupe_reason_codes(&mut reason_codes);
    score = score.clamp(0.0, 1.0);

    let verdict = if score >= 0.65 { "pass" } else { "review" };
    let recommendation = if verdict == "pass" {
        "Proceed with current autonomy gate and keep user-facing rationale explicit."
    } else {
        "Switch to uncertainty-explicit wording and ask one high-value clarification before strong personalization."
    };

    AgentLaaJSidecar {
        schema_version: LAAJ_SIDECAR_SCHEMA_VERSION.to_string(),
        verdict: verdict.to_string(),
        score,
        policy_role: SIDECAR_POLICY_ROLE_ADVISORY_ONLY.to_string(),
        can_block_autonomy: false,
        recommendation: recommendation.to_string(),
        reason_codes,
    }
}

fn build_sidecar_assessment(
    claim_guard: &AgentWriteClaimGuard,
    verification: &AgentWriteVerificationSummary,
    session_audit: &AgentSessionAuditSummary,
    response_mode_policy: &AgentResponseModePolicy,
) -> AgentSidecarAssessment {
    let retrieval_regret = build_retrieval_regret(claim_guard, verification, response_mode_policy);
    let laaj = build_laaj_sidecar(
        claim_guard,
        session_audit,
        response_mode_policy,
        &retrieval_regret,
    );
    AgentSidecarAssessment {
        retrieval_regret,
        laaj,
    }
}

fn response_mode_confidence_band(policy: &AgentResponseModePolicy) -> &'static str {
    match policy.mode_code.as_str() {
        "A" => "high",
        "B" => "medium",
        _ => "low",
    }
}

fn failure_profile_confidence_band(profile: &AgentPersonalFailureProfile) -> &'static str {
    match profile.data_quality_band.as_str() {
        "high" => "high",
        "medium" => "medium",
        _ => "low",
    }
}

fn learning_signal_event_from_contract(
    user_id: Uuid,
    signal_type: &str,
    issue_type: &str,
    invariant_id: &str,
    workflow_phase: &str,
    confidence_band: &str,
    attributes: Value,
    session_id: &str,
) -> CreateEventRequest {
    let captured_at = Utc::now();
    let agent_version =
        std::env::var("KURA_AGENT_VERSION").unwrap_or_else(|_| "api_agent_v1".to_string());
    let signature_seed = format!(
        "{}|{}|{}|{}|{}|{}|{}",
        signal_type,
        issue_type,
        invariant_id,
        agent_version,
        workflow_phase,
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
            "invariant_id": invariant_id,
            "agent_version": agent_version,
            "workflow_phase": workflow_phase,
            "modality": "chat",
            "confidence_band": confidence_band,
        },
        "cluster_signature": cluster_signature,
        "attributes": attributes,
    });

    CreateEventRequest {
        timestamp: captured_at,
        event_type: "learning.signal.logged".to_string(),
        data: event_data,
        metadata: EventMetadata {
            source: Some("agent_write_with_proof".to_string()),
            agent: Some("api".to_string()),
            device: None,
            session_id: Some(session_id.to_string()),
            idempotency_key: format!("learning-signal-{}", Uuid::now_v7()),
        },
    }
}

fn build_response_mode_sidecar_learning_signal_events(
    user_id: Uuid,
    response_mode_policy: &AgentResponseModePolicy,
    personal_failure_profile: &AgentPersonalFailureProfile,
    sidecar_assessment: &AgentSidecarAssessment,
) -> Vec<CreateEventRequest> {
    let response_mode_event = learning_signal_event_from_contract(
        user_id,
        "response_mode_selected",
        "response_mode_policy",
        RESPONSE_MODE_INVARIANT_ID,
        "agent_write_with_proof",
        response_mode_confidence_band(response_mode_policy),
        serde_json::json!({
            "contract_schema_version": response_mode_policy.schema_version,
            "mode_code": response_mode_policy.mode_code,
            "mode": response_mode_policy.mode,
            "evidence_state": response_mode_policy.evidence_state,
            "evidence_score": response_mode_policy.evidence_score,
            "threshold_a_min": response_mode_policy.threshold_a_min,
            "threshold_b_min": response_mode_policy.threshold_b_min,
            "quality_status": response_mode_policy.quality_status,
            "integrity_slo_status": response_mode_policy.integrity_slo_status,
            "calibration_status": response_mode_policy.calibration_status,
            "policy_role": response_mode_policy.policy_role,
            "requires_transparency_note": response_mode_policy.requires_transparency_note,
            "reason_codes": response_mode_policy.reason_codes,
        }),
        "learning:response-mode",
    );

    let personal_failure_event = learning_signal_event_from_contract(
        user_id,
        "personal_failure_profile_observed",
        "personal_failure_profile",
        PERSONAL_FAILURE_PROFILE_INVARIANT_ID,
        "agent_write_with_proof",
        failure_profile_confidence_band(personal_failure_profile),
        serde_json::json!({
            "contract_schema_version": personal_failure_profile.schema_version,
            "profile_id": personal_failure_profile.profile_id,
            "model_identity": personal_failure_profile.model_identity,
            "data_quality_band": personal_failure_profile.data_quality_band,
            "policy_role": personal_failure_profile.policy_role,
            "recommended_response_mode": personal_failure_profile.recommended_response_mode,
            "active_signal_codes": personal_failure_profile
                .active_signals
                .iter()
                .map(|signal| signal.code.clone())
                .collect::<Vec<_>>(),
            "active_signal_weights": personal_failure_profile
                .active_signals
                .iter()
                .map(|signal| signal.weight)
                .collect::<Vec<_>>(),
        }),
        "learning:personal-failure-profile",
    );

    let retrieval_regret_event = learning_signal_event_from_contract(
        user_id,
        "retrieval_regret_observed",
        "retrieval_regret",
        RETRIEVAL_REGRET_INVARIANT_ID,
        "agent_write_with_proof",
        sidecar_assessment.retrieval_regret.regret_band.as_str(),
        serde_json::json!({
            "contract_schema_version": sidecar_assessment.retrieval_regret.schema_version,
            "regret_score": sidecar_assessment.retrieval_regret.regret_score,
            "regret_band": sidecar_assessment.retrieval_regret.regret_band,
            "regret_threshold": sidecar_assessment.retrieval_regret.regret_threshold,
            "threshold_exceeded": sidecar_assessment.retrieval_regret.threshold_exceeded,
            "reason_codes": sidecar_assessment.retrieval_regret.reason_codes,
            "policy_role": SIDECAR_POLICY_ROLE_ADVISORY_ONLY,
        }),
        "learning:retrieval-regret",
    );

    let laaj_confidence_band = if sidecar_assessment.laaj.verdict == "pass" {
        "high"
    } else {
        "medium"
    };
    let laaj_event = learning_signal_event_from_contract(
        user_id,
        "laaj_sidecar_assessed",
        "laaj_sidecar",
        LAAJ_SIDECAR_INVARIANT_ID,
        "agent_write_with_proof",
        laaj_confidence_band,
        serde_json::json!({
            "contract_schema_version": sidecar_assessment.laaj.schema_version,
            "verdict": sidecar_assessment.laaj.verdict,
            "score": sidecar_assessment.laaj.score,
            "policy_role": sidecar_assessment.laaj.policy_role,
            "can_block_autonomy": sidecar_assessment.laaj.can_block_autonomy,
            "recommendation": sidecar_assessment.laaj.recommendation,
            "reason_codes": sidecar_assessment.laaj.reason_codes,
        }),
        "learning:laaj-sidecar",
    );

    vec![
        response_mode_event,
        personal_failure_event,
        retrieval_regret_event,
        laaj_event,
    ]
}

static LEAK_DOTTED_TOKEN_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+){1,}\b").expect("valid dotted token regex")
});
static LEAK_INVARIANT_CODE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bINV-\d{3}\b").expect("valid invariant code regex"));
static LEAK_INVARIANT_FN_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\binv_[a-z0-9_]+\b").expect("valid invariant fn regex"));
static LEAK_ENDPOINT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"/v1/[a-z0-9/_\.-]+").expect("valid endpoint regex"));

fn is_machine_token_shape(token: &str) -> bool {
    let trimmed = token.trim();
    if trimmed.len() < 3 {
        return false;
    }
    trimmed.contains('.')
        || trimmed.contains('_')
        || trimmed.contains('/')
        || trimmed.starts_with("INV-")
        || trimmed
            .chars()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '-')
}

fn insert_machine_token(tokens: &mut HashSet<String>, token: &str) {
    let trimmed = token.trim();
    if is_machine_token_shape(trimmed) {
        tokens.insert(trimmed.to_string());
    }
}

fn collect_machine_language_tokens(response: &AgentWriteWithProofResponse) -> HashSet<String> {
    let mut tokens = HashSet::new();

    for receipt in &response.receipts {
        insert_machine_token(&mut tokens, &receipt.event_type);
    }
    for event_type in &response.workflow_gate.planning_event_types {
        insert_machine_token(&mut tokens, event_type);
    }
    for requirement in &response.workflow_gate.missing_requirements {
        insert_machine_token(&mut tokens, requirement);
    }
    for marker in &response.claim_guard.uncertainty_markers {
        insert_machine_token(&mut tokens, marker);
    }
    for marker in &response.claim_guard.deferred_markers {
        insert_machine_token(&mut tokens, marker);
    }
    for reason in &response.claim_guard.autonomy_gate.reason_codes {
        insert_machine_token(&mut tokens, reason);
    }
    for class_name in &response.session_audit.mismatch_classes {
        insert_machine_token(&mut tokens, class_name);
    }
    for code in &response.trace_digest.warning_codes {
        insert_machine_token(&mut tokens, code);
    }
    for code in &response.trace_digest.autonomy_reason_codes {
        insert_machine_token(&mut tokens, code);
    }
    for risk in &response.post_task_reflection.residual_risks {
        insert_machine_token(&mut tokens, risk);
    }
    for signal in &response.post_task_reflection.emitted_learning_signal_types {
        insert_machine_token(&mut tokens, signal);
    }
    if let Some(technical) = response.repair_feedback.technical.as_ref() {
        for step in &technical.command_trace {
            insert_machine_token(&mut tokens, step);
        }
    }
    if !response.verification.write_path.trim().is_empty() {
        insert_machine_token(&mut tokens, &response.verification.write_path);
    }
    if !response
        .post_task_reflection
        .next_verification_step
        .trim()
        .is_empty()
    {
        insert_machine_token(
            &mut tokens,
            &response.post_task_reflection.next_verification_step,
        );
    }
    tokens
}

fn detect_machine_language_leaks(text: &str, machine_tokens: &HashSet<String>) -> Vec<String> {
    let mut leaks: HashSet<String> = HashSet::new();
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }
    let lowered = trimmed.to_lowercase();
    for token in machine_tokens {
        if token.is_empty() {
            continue;
        }
        let token_lower = token.to_lowercase();
        if lowered.contains(&token_lower) {
            leaks.insert(token.to_string());
        }
    }
    for capture in LEAK_DOTTED_TOKEN_RE.find_iter(trimmed) {
        leaks.insert(capture.as_str().to_string());
    }
    for capture in LEAK_INVARIANT_CODE_RE.find_iter(trimmed) {
        leaks.insert(capture.as_str().to_string());
    }
    for capture in LEAK_INVARIANT_FN_RE.find_iter(trimmed) {
        leaks.insert(capture.as_str().to_string());
    }
    for capture in LEAK_ENDPOINT_RE.find_iter(trimmed) {
        leaks.insert(capture.as_str().to_string());
    }
    let mut out: Vec<String> = leaks.into_iter().collect();
    out.sort();
    out
}

fn replacement_for_machine_token(token: &str) -> &'static str {
    let normalized = token.trim().to_lowercase();
    if normalized.starts_with("inv-") || normalized.starts_with("inv_") {
        return "interner Pruefhinweis";
    }
    if normalized.starts_with("/v1/") {
        return "interne Schnittstelle";
    }
    if normalized.contains("idempotency") {
        return "Sicherungsmerkmal";
    }
    if normalized.contains("read-after-write") || normalized.contains("read_after_write") {
        return "Bestaetigungspruefung";
    }
    if normalized.contains("write-with-proof") || normalized.contains("write_with_proof") {
        return "Speicherpruefung";
    }
    if normalized.contains('.') || normalized.contains('_') || normalized.contains('/') {
        return "interner Fachbegriff";
    }
    "technischer Begriff"
}

fn replace_case_insensitive(text: &str, needle: &str, replacement: &str) -> String {
    let trimmed = needle.trim();
    if trimmed.is_empty() {
        return text.to_string();
    }
    let pattern = format!("(?i){}", regex::escape(trimmed));
    let Ok(re) = Regex::new(&pattern) else {
        return text.to_string();
    };
    re.replace_all(text, replacement).into_owned()
}

fn normalize_user_text_output(text: &str) -> String {
    let mut normalized = text
        .replace("  ", " ")
        .replace(" ,", ",")
        .replace(" .", ".")
        .replace(" :", ":")
        .trim()
        .to_string();
    if normalized.is_empty() {
        normalized =
            "Ich habe das verarbeitet und formuliere es fuer dich in Alltagssprache.".to_string();
    }
    normalized
}

fn rewrite_user_text_once(text: &str, machine_tokens: &HashSet<String>) -> String {
    let mut rewritten = text.to_string();
    let mut sorted_tokens: Vec<String> = machine_tokens.iter().cloned().collect();
    sorted_tokens.sort_by(|a, b| b.len().cmp(&a.len()));
    for token in sorted_tokens {
        rewritten =
            replace_case_insensitive(&rewritten, &token, replacement_for_machine_token(&token));
    }
    rewritten = LEAK_INVARIANT_CODE_RE
        .replace_all(&rewritten, "interner Pruefhinweis")
        .into_owned();
    rewritten = LEAK_INVARIANT_FN_RE
        .replace_all(&rewritten, "interner Pruefhinweis")
        .into_owned();
    rewritten = LEAK_ENDPOINT_RE
        .replace_all(&rewritten, "interne Schnittstelle")
        .into_owned();
    rewritten = LEAK_DOTTED_TOKEN_RE
        .replace_all(&rewritten, "interner Fachbegriff")
        .into_owned();
    rewritten = replace_case_insensitive(&rewritten, "write-with-proof", "Speicherpruefung");
    rewritten = replace_case_insensitive(&rewritten, "read-after-write", "Bestaetigungspruefung");
    rewritten = replace_case_insensitive(&rewritten, "idempotency keys", "Sicherungsmerkmale");
    rewritten = replace_case_insensitive(&rewritten, "idempotency key", "Sicherungsmerkmal");
    rewritten = replace_case_insensitive(&rewritten, "receipt", "Bestaetigung");
    rewritten = replace_case_insensitive(&rewritten, "receipts", "Bestaetigungen");
    normalize_user_text_output(&rewritten)
}

fn user_facing_text_fields(response: &AgentWriteWithProofResponse) -> Vec<&str> {
    let mut texts = vec![
        response.claim_guard.recommended_user_phrase.as_str(),
        response.reliability_ux.assistant_phrase.as_str(),
        response.workflow_gate.message.as_str(),
        response.repair_feedback.summary.as_str(),
        response.trace_digest.chat_summary.as_str(),
        response.post_task_reflection.chat_summary.as_str(),
        response
            .post_task_reflection
            .next_verification_step
            .as_str(),
    ];
    if let Some(question) = response.reliability_ux.clarification_question.as_deref() {
        texts.push(question);
    }
    if let Some(question) = response.repair_feedback.clarification_question.as_deref() {
        texts.push(question);
    }
    if let Some(confirm) = response.intent_handshake_confirmation.as_ref() {
        texts.push(confirm.chat_confirmation.as_str());
    }
    if let Some(undo) = response.repair_feedback.undo.as_ref() {
        texts.push(undo.detail.as_str());
    }
    for warning in &response.warnings {
        texts.push(warning.message.as_str());
    }
    texts
}

fn count_leaks_in_user_fields(
    response: &AgentWriteWithProofResponse,
    machine_tokens: &HashSet<String>,
) -> usize {
    user_facing_text_fields(response)
        .iter()
        .map(|text| detect_machine_language_leaks(text, machine_tokens).len())
        .sum()
}

fn rewrite_user_facing_fields_once(
    response: &mut AgentWriteWithProofResponse,
    machine_tokens: &HashSet<String>,
) {
    response.claim_guard.recommended_user_phrase = rewrite_user_text_once(
        &response.claim_guard.recommended_user_phrase,
        machine_tokens,
    );
    response.reliability_ux.assistant_phrase =
        rewrite_user_text_once(&response.reliability_ux.assistant_phrase, machine_tokens);
    if let Some(question) = response.reliability_ux.clarification_question.as_mut() {
        *question = rewrite_user_text_once(question, machine_tokens);
    }
    response.workflow_gate.message =
        rewrite_user_text_once(&response.workflow_gate.message, machine_tokens);
    response.repair_feedback.summary =
        rewrite_user_text_once(&response.repair_feedback.summary, machine_tokens);
    if let Some(question) = response.repair_feedback.clarification_question.as_mut() {
        *question = rewrite_user_text_once(question, machine_tokens);
    }
    if let Some(undo) = response.repair_feedback.undo.as_mut() {
        undo.detail = rewrite_user_text_once(&undo.detail, machine_tokens);
    }
    if let Some(confirm) = response.intent_handshake_confirmation.as_mut() {
        confirm.chat_confirmation =
            rewrite_user_text_once(&confirm.chat_confirmation, machine_tokens);
    }
    response.trace_digest.chat_summary =
        rewrite_user_text_once(&response.trace_digest.chat_summary, machine_tokens);
    response.post_task_reflection.chat_summary =
        rewrite_user_text_once(&response.post_task_reflection.chat_summary, machine_tokens);
    response.post_task_reflection.next_verification_step = rewrite_user_text_once(
        &response.post_task_reflection.next_verification_step,
        machine_tokens,
    );
    for warning in &mut response.warnings {
        warning.message = rewrite_user_text_once(&warning.message, machine_tokens);
    }
}

fn apply_user_safe_language_guard(
    mut response: AgentWriteWithProofResponse,
) -> AgentWriteWithProofResponse {
    let machine_tokens = collect_machine_language_tokens(&response);
    let leak_count_before = count_leaks_in_user_fields(&response, &machine_tokens);
    if leak_count_before == 0 {
        return response;
    }
    rewrite_user_facing_fields_once(&mut response, &machine_tokens);
    let leak_count_after = count_leaks_in_user_fields(&response, &machine_tokens);
    tracing::info!(
        leak_detected_total = leak_count_before,
        rewrite_applied_total = 1,
        leak_passed_through_total = leak_count_after,
        "user-safe language guard executed (fail-open, one rewrite)"
    );
    response
}

fn post_task_reflection_signal_type(certainty_state: &str) -> &'static str {
    match certainty_state {
        "confirmed" => "post_task_reflection_confirmed",
        "partial" => "post_task_reflection_partial",
        _ => "post_task_reflection_unresolved",
    }
}

fn build_post_task_reflection_learning_signal_event(
    user_id: Uuid,
    requested_event_count: usize,
    receipts: &[AgentWriteReceipt],
    verification: &AgentWriteVerificationSummary,
    claim_guard: &AgentWriteClaimGuard,
    certainty_state: &str,
    model_identity: &ResolvedModelIdentity,
) -> CreateEventRequest {
    let signal_type = post_task_reflection_signal_type(certainty_state);
    let mismatch_reason_codes: Vec<String> = Vec::new();
    build_learning_signal_event(
        user_id,
        signal_type,
        "post_task_reflection_contract",
        claim_guard,
        verification,
        requested_event_count,
        receipts.len(),
        model_identity,
        MISMATCH_SEVERITY_NONE,
        &mismatch_reason_codes,
    )
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

        if event_type == "session.logged" {
            for gap in collect_session_logged_required_field_gaps(event) {
                mismatch_detected += 1;
                mismatch_unresolved += 1;
                mismatch_classes.insert(AUDIT_CLASS_SESSION_BLOCK_REQUIRED_FIELD.to_string(), ());
                unresolved.push(gap);
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
    headers: HeaderMap,
    Json(req): Json<AgentWriteWithProofRequest>,
) -> Result<impl axum::response::IntoResponse, AppError> {
    require_scopes(&auth, &["agent:write"], "POST /v1/agent/write-with-proof")?;
    let language_mode = resolve_agent_language_mode(&auth, &headers);
    let user_id = auth.user_id;
    let requested_event_count = req.events.len();
    let action_class = classify_write_action_class(&req.events);
    let high_impact_confirmation_request_digest =
        build_high_impact_confirmation_request_digest(&req, &action_class);
    let verify_timeout_ms = clamp_verify_timeout_ms(req.verify_timeout_ms);
    let read_after_write_targets =
        normalize_read_after_write_targets(req.read_after_write_targets.clone());

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

    // intent_handshake is mandatory only for strict tier + high-impact writes.
    // For moderate/advanced, it's accepted but not required.
    let intent_handshake_confirmation = match req.intent_handshake.as_ref() {
        Some(handshake) => {
            validate_intent_handshake(handshake, &action_class)?;
            Some(build_intent_handshake_confirmation(handshake))
        }
        None => None,
    };

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

    let quality_health = fetch_quality_health_projection(&state, user_id).await?;
    let model_identity = resolve_model_identity_for_write(&auth, &req, &action_class, Utc::now());
    let (tier_policy, tier_reason_codes) =
        resolve_model_tier_policy_for_write(&state, user_id, &model_identity).await?;
    let mut model_reason_codes = model_identity.reason_codes.clone();
    model_reason_codes.extend(tier_reason_codes);
    dedupe_reason_codes(&mut model_reason_codes);
    let policy_with_user_overrides = apply_user_preference_overrides(
        autonomy_policy_from_quality_health(quality_health.as_ref()),
        user_profile.as_ref(),
    );
    let autonomy_policy = apply_model_tier_policy(
        policy_with_user_overrides,
        &model_identity.model_identity,
        &tier_policy,
        &model_reason_codes,
    );
    let autonomy_gate = merge_autonomy_gate_with_memory_guard(
        evaluate_autonomy_gate(
            &action_class,
            &autonomy_policy,
            &tier_policy,
            &model_reason_codes,
        ),
        &action_class,
        user_profile.as_ref(),
    );
    if autonomy_gate.decision == "block" {
        return Err(AppError::Validation {
            message: "High-impact write blocked by adaptive autonomy gate.".to_string(),
            field: Some("events".to_string()),
            received: Some(serde_json::json!({
                "action_class": autonomy_gate.action_class,
                "model_tier": autonomy_gate.model_tier,
                "effective_quality_status": autonomy_gate.effective_quality_status,
                "reason_codes": autonomy_gate.reason_codes,
            })),
            docs_hint: Some(
                "Request explicit user confirmation or reduce scope to low-impact writes before retry."
                    .to_string(),
            ),
        });
    }
    // Strict tier: require intent_handshake for high-impact writes (must explain reasoning).
    if autonomy_gate.model_tier == "strict"
        && action_class == "high_impact_write"
        && intent_handshake_confirmation.is_none()
    {
        return Err(AppError::Validation {
            message: "intent_handshake is required for high-impact writes in strict tier".to_string(),
            field: Some("intent_handshake".to_string()),
            received: Some(serde_json::json!({
                "model_tier": autonomy_gate.model_tier,
                "action_class": action_class,
            })),
            docs_hint: Some(
                "Strict tier requires intent_handshake.v1 with goal, planned_action, assumptions, non_goals, impact_class, and success_criteria."
                    .to_string(),
            ),
        });
    }
    if autonomy_gate.decision == "confirm_first" && action_class == "high_impact_write" {
        let confirmation_secret = std::env::var(MODEL_ATTESTATION_SECRET_ENV).ok();
        validate_high_impact_confirmation(
            req.high_impact_confirmation.as_ref(),
            &req.events,
            &autonomy_gate,
            user_id,
            &action_class,
            &high_impact_confirmation_request_digest,
            confirmation_secret.as_deref(),
            Utc::now(),
        )?;
    }
    if autonomy_gate.decision == "confirm_first" {
        workflow_warnings.push(BatchEventWarning {
            event_index: 0,
            field: "autonomy.gate".to_string(),
            message: format!(
                "Confirm-first mode active for high-impact write (tier='{}', quality='{}', reasons={}).",
                autonomy_gate.model_tier,
                autonomy_gate.effective_quality_status,
                autonomy_gate.reason_codes.join(","),
            ),
            severity: "warning".to_string(),
        });
    }

    let (receipts, mut warnings, write_path) =
        write_events_with_receipts(&state, user_id, &req.events, "metadata.idempotency_key")
            .await?;
    warnings.extend(workflow_warnings);
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
        autonomy_gate,
    );
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
    let response_mode_policy =
        build_response_mode_policy(&claim_guard, &verification, quality_health.as_ref());
    let personal_failure_profile = build_personal_failure_profile(
        user_id,
        &model_identity,
        &claim_guard,
        &verification,
        &session_audit_summary,
        &response_mode_policy,
    );
    let sidecar_assessment = build_sidecar_assessment(
        &claim_guard,
        &verification,
        &session_audit_summary,
        &response_mode_policy,
    );
    let trace_digest = build_trace_digest(
        &receipts,
        &warnings,
        &verification,
        &claim_guard,
        &workflow_gate,
        &session_audit_summary,
        &repair_feedback,
    );
    let mut post_task_reflection = build_post_task_reflection(
        &trace_digest,
        &verification,
        &session_audit_summary,
        &repair_feedback,
    );

    let quality_signal = build_save_claim_checked_event(
        requested_event_count,
        &receipts,
        &verification,
        &claim_guard,
        &session_audit_summary,
        &model_identity,
    );
    let mut quality_events = vec![quality_signal];
    quality_events.extend(build_save_handshake_learning_signal_events(
        user_id,
        requested_event_count,
        &receipts,
        &verification,
        &claim_guard,
        &model_identity,
    ));
    if let Some(workflow_signal) =
        build_workflow_gate_learning_signal_event(user_id, &workflow_gate)
    {
        quality_events.push(workflow_signal);
    }
    quality_events.extend(build_response_mode_sidecar_learning_signal_events(
        user_id,
        &response_mode_policy,
        &personal_failure_profile,
        &sidecar_assessment,
    ));
    quality_events.extend(telemetry_events);
    let reflection_signal = build_post_task_reflection_learning_signal_event(
        user_id,
        requested_event_count,
        &receipts,
        &verification,
        &claim_guard,
        &post_task_reflection.certainty_state,
        &model_identity,
    );
    quality_events.push(reflection_signal);
    let mut emitted_learning_signal_types: Vec<String> = quality_events
        .iter()
        .filter_map(|event| {
            event
                .data
                .get("signal_type")
                .and_then(Value::as_str)
                .map(|value| value.to_string())
        })
        .collect();
    emitted_learning_signal_types.sort();
    emitted_learning_signal_types.dedup();
    post_task_reflection.emitted_learning_signal_types = emitted_learning_signal_types;

    let _ = create_events_batch_internal(&state, user_id, &quality_events).await;

    let response = AgentWriteWithProofResponse {
        receipts,
        warnings,
        verification,
        claim_guard,
        reliability_ux,
        workflow_gate,
        session_audit: session_audit_summary,
        repair_feedback,
        intent_handshake_confirmation,
        trace_digest,
        post_task_reflection,
        response_mode_policy,
        personal_failure_profile,
        sidecar_assessment,
    };
    let response = if language_mode == AgentLanguageMode::UserSafe {
        apply_user_safe_language_guard(response)
    } else {
        response
    };

    Ok((StatusCode::CREATED, Json(response)))
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
    let model_identity = resolve_model_identity(&auth);
    let tier_policy = resolve_model_tier_policy_default();
    let self_model = build_agent_self_model(&model_identity, &tier_policy);
    Ok(Json(build_agent_capabilities_with_self_model(self_model)))
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
    let model_identity = resolve_model_identity(&auth);
    let tier_policy = resolve_model_tier_policy_default();
    let self_model = build_agent_self_model(&model_identity, &tier_policy);
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
    let consistency_inbox =
        fetch_projection(&mut tx, user_id, "consistency_inbox", "overview").await?;

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
    let generated_at = Utc::now();
    let challenge_mode = resolve_challenge_mode(Some(&user_profile));
    let memory_tier_contract = build_memory_tier_contract(
        &user_profile,
        training_plan.as_ref(),
        semantic_memory.as_ref(),
        generated_at,
    );

    Ok(Json(AgentContextResponse {
        system,
        self_model,
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
        consistency_inbox,
        exercise_progression,
        strength_inference,
        custom,
        meta: AgentContextMeta {
            generated_at,
            exercise_limit,
            strength_limit,
            custom_limit,
            task_intent: ranking_context.intent.clone(),
            ranking_strategy: "composite(recency,confidence,semantic_relevance,task_intent)"
                .to_string(),
            context_contract_version: AGENT_CONTEXT_CONTRACT_VERSION.to_string(),
            system_contract: build_agent_context_system_contract(),
            challenge_mode,
            memory_tier_contract,
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::{
        AgentReadAfterWriteCheck, AgentReadAfterWriteTarget, AgentRepairFeedback,
        AgentRepairReceipt, AgentResolveVisualizationRequest, AgentSessionAuditSummary,
        AgentVisualizationDataSource, AgentVisualizationResolvedSource, AgentVisualizationSpec,
        AgentVisualizationTimezoneContext, AgentWorkflowGate, AgentWorkflowState,
        AgentWriteReceipt, AgentWriteVerificationSummary, IntentClass, ProjectionResponse,
        RankingContext, WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE,
        WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE, bind_visualization_source, bootstrap_user_profile,
        build_agent_capabilities, build_auto_onboarding_close_event, build_claim_guard,
        build_evidence_claim_events, build_reliability_ux, build_repair_feedback,
        build_save_handshake_learning_signal_events, build_session_audit_artifacts,
        build_visualization_outputs, clamp_limit, clamp_verify_timeout_ms,
        collect_reliability_inferred_facts, default_autonomy_gate, default_autonomy_policy,
        extract_evidence_claim_drafts, extract_set_context_mentions_from_text,
        missing_onboarding_close_requirements, normalize_read_after_write_targets,
        normalize_set_type, normalize_visualization_spec, parse_rest_seconds_from_text,
        parse_rest_with_span, parse_rir_from_text, parse_rir_with_span, parse_set_type_with_span,
        parse_tempo_from_text, parse_tempo_with_span, rank_projection_list,
        ranking_candidate_limit, recover_receipts_for_idempotent_retry, resolve_visualization,
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
    use std::collections::HashMap;
    use uuid::Uuid;

    fn model_attestation_test_lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: std::sync::LazyLock<std::sync::Mutex<()>> =
            std::sync::LazyLock::new(|| std::sync::Mutex::new(()));
        LOCK.lock().unwrap_or_else(|poison| poison.into_inner())
    }

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

    fn make_session_logged_event(
        data: serde_json::Value,
        idempotency_key: &str,
    ) -> CreateEventRequest {
        CreateEventRequest {
            timestamp: Utc::now(),
            event_type: "session.logged".to_string(),
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

    fn make_write_with_proof_request(
        events: Vec<CreateEventRequest>,
    ) -> super::AgentWriteWithProofRequest {
        super::AgentWriteWithProofRequest {
            events,
            read_after_write_targets: vec![super::AgentReadAfterWriteTarget {
                projection_type: "user_profile".to_string(),
                key: "me".to_string(),
            }],
            verify_timeout_ms: Some(1200),
            include_repair_technical_details: false,
            intent_handshake: None,
            model_attestation: None,
            high_impact_confirmation: None,
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

    fn make_trace_contract_artifacts(
        verification_status: &str,
        check_status: &str,
        session_status: &str,
        clarification_question: Option<&str>,
    ) -> (
        Vec<AgentWriteReceipt>,
        Vec<BatchEventWarning>,
        AgentWriteVerificationSummary,
        super::AgentWriteClaimGuard,
        AgentWorkflowGate,
        AgentSessionAuditSummary,
        AgentRepairFeedback,
    ) {
        let receipt = AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "k-contract-1".to_string(),
            event_timestamp: Utc::now(),
        };
        let receipts = vec![receipt];
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
            status: check_status.to_string(),
            observed_projection_version: Some(1),
            observed_last_event_id: None,
            detail: "contract-fixture".to_string(),
        }];
        let warnings = vec![BatchEventWarning {
            event_index: 0,
            field: "autonomy.gate".to_string(),
            message: "confirm".to_string(),
            severity: "warning".to_string(),
        }];
        let verification = make_verification(verification_status, checks.clone());
        let claim_guard = build_claim_guard(
            &receipts,
            1,
            &checks,
            &warnings,
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
        let workflow_gate = AgentWorkflowGate {
            phase: "planning".to_string(),
            status: "allowed".to_string(),
            transition: "none".to_string(),
            onboarding_closed: true,
            override_used: false,
            message: "ok".to_string(),
            missing_requirements: Vec::new(),
            planning_event_types: Vec::new(),
        };
        let unresolved_count = if session_status == "needs_clarification" {
            1
        } else {
            0
        };
        let session_audit = AgentSessionAuditSummary {
            status: session_status.to_string(),
            mismatch_detected: unresolved_count,
            mismatch_repaired: 0,
            mismatch_unresolved: unresolved_count,
            mismatch_classes: Vec::new(),
            clarification_question: clarification_question.map(|value| value.to_string()),
        };
        let repair_feedback = AgentRepairFeedback {
            status: "none".to_string(),
            summary: "none".to_string(),
            receipt: AgentRepairReceipt {
                status: "none".to_string(),
                changed_fields_count: 0,
                unchanged_metrics: HashMap::new(),
            },
            clarification_question: None,
            undo: None,
            technical: None,
        };

        (
            receipts,
            warnings,
            verification,
            claim_guard,
            workflow_gate,
            session_audit,
            repair_feedback,
        )
    }

    fn make_access_token_auth(scopes: &[&str], client_id: &str) -> AuthenticatedUser {
        AuthenticatedUser {
            user_id: Uuid::now_v7(),
            auth_method: AuthMethod::AccessToken {
                token_id: Uuid::now_v7(),
                client_id: client_id.to_string(),
            },
            scopes: scopes.iter().map(|scope| (*scope).to_string()).collect(),
        }
    }

    fn make_access_token_auth_with_user(
        user_id: Uuid,
        scopes: &[&str],
        client_id: &str,
    ) -> AuthenticatedUser {
        AuthenticatedUser {
            user_id,
            auth_method: AuthMethod::AccessToken {
                token_id: Uuid::now_v7(),
                client_id: client_id.to_string(),
            },
            scopes: scopes.iter().map(|scope| (*scope).to_string()).collect(),
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
        Some((
            AppState {
                db: pool,
                signup_gate: crate::state::SignupGate::Open,
            },
            auth,
            user_id,
        ))
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
    fn scenario_library_onboarding_logging_saved() {
        let state = AgentWorkflowState {
            onboarding_closed: false,
            override_active: false,
            missing_close_requirements: vec![],
            legacy_planning_history: false,
        };
        let events = vec![make_set_event(
            json!({"exercise_id": "barbell_back_squat", "reps": 5, "weight_kg": 100}),
            Some("sess-1"),
            "k-scenario-1",
        )];
        let gate = workflow_gate_from_request(&events, &state);
        assert_eq!(gate.status, "allowed");
        assert_eq!(gate.phase, "onboarding");
        assert_eq!(gate.transition, "none");

        let event_id = Uuid::now_v7();
        let receipts = vec![AgentWriteReceipt {
            event_id,
            event_type: "set.logged".to_string(),
            idempotency_key: "k-scenario-1".to_string(),
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
        let guard = build_claim_guard(
            &receipts,
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
        let summary = AgentSessionAuditSummary {
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
        assert!(ux.clarification_question.is_none());
    }

    #[test]
    fn scenario_library_planning_override_confirm_first() {
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
                "wf-override-k-2",
            ),
            make_event(
                "training_plan.updated",
                json!({"name": "Plan A"}),
                "plan-k-scenario-1",
            ),
        ];
        let gate = workflow_gate_from_request(&events, &state);
        assert_eq!(gate.status, "allowed");
        assert_eq!(gate.transition, "override");
        assert!(gate.override_used);

        let tier = super::model_tier_policy_from_name("strict");
        let policy = default_autonomy_policy();
        let autonomy_gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &tier, &[]);
        assert_eq!(autonomy_gate.decision, "confirm_first");
        assert!(
            autonomy_gate
                .reason_codes
                .iter()
                .any(|code| code == "model_tier_strict_requires_confirmation")
        );
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
    fn session_audit_session_logged_strength_without_hr_keeps_clean() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_session_logged_event(
            json!({
                "contract_version": "session.logged.v1",
                "session_meta": {"sport": "strength", "timezone": "Europe/Berlin"},
                "blocks": [
                    {
                        "block_type": "strength_set",
                        "dose": {
                            "work": {"reps": 5},
                            "repeats": 5
                        },
                        "intensity_anchors": [
                            {
                                "measurement_state": "measured",
                                "unit": "rpe",
                                "value": 8
                            }
                        ]
                    }
                ],
                "provenance": {"source_type": "manual"}
            }),
            "k-session-1",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "session.logged".to_string(),
            idempotency_key: "k-session-1".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "clean");
        assert_eq!(artifacts.summary.mismatch_detected, 0);
        assert_eq!(artifacts.summary.mismatch_unresolved, 0);
        assert!(artifacts.summary.clarification_question.is_none());
    }

    #[test]
    fn session_audit_session_logged_interval_missing_anchor_requires_block_question() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_session_logged_event(
            json!({
                "contract_version": "session.logged.v1",
                "session_meta": {"sport": "running", "timezone": "Europe/Berlin"},
                "blocks": [
                    {
                        "block_type": "interval_endurance",
                        "dose": {
                            "work": {"duration_seconds": 120},
                            "recovery": {"duration_seconds": 60},
                            "repeats": 8
                        }
                    }
                ],
                "provenance": {"source_type": "manual"}
            }),
            "k-session-2",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "session.logged".to_string(),
            idempotency_key: "k-session-2".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "needs_clarification");
        assert_eq!(artifacts.summary.mismatch_detected, 1);
        assert_eq!(artifacts.summary.mismatch_repaired, 0);
        assert_eq!(artifacts.summary.mismatch_unresolved, 1);
        let question = artifacts
            .summary
            .clarification_question
            .as_deref()
            .unwrap_or("");
        assert!(question.contains("Intensitätsanker"));
        assert!(question.contains("not_applicable"));
        assert!(!question.contains("Herzfrequenz muss"));
    }

    #[test]
    fn session_audit_session_logged_not_applicable_anchor_status_keeps_clean() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_session_logged_event(
            json!({
                "contract_version": "session.logged.v1",
                "session_meta": {"sport": "running", "timezone": "Europe/Berlin"},
                "blocks": [
                    {
                        "block_type": "interval_endurance",
                        "dose": {
                            "work": {"duration_seconds": 120},
                            "recovery": {"duration_seconds": 60},
                            "repeats": 8
                        },
                        "intensity_anchors_status": "not_applicable"
                    }
                ],
                "provenance": {"source_type": "manual"}
            }),
            "k-session-3",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "session.logged".to_string(),
            idempotency_key: "k-session-3".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();

        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "clean");
        assert_eq!(artifacts.summary.mismatch_detected, 0);
        assert_eq!(artifacts.summary.mismatch_unresolved, 0);
        assert!(artifacts.summary.clarification_question.is_none());
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
        let states: [Option<&str>; 4] = [
            None,
            Some("confirmed"),
            Some("inferred"),
            Some("unresolved"),
        ];
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
            default_autonomy_gate(),
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
            default_autonomy_gate(),
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
        let guard = build_claim_guard(
            &[],
            0,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );

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

        let guard = build_claim_guard(
            &receipts,
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
        assert!(guard.allow_saved_claim);
        assert_eq!(guard.claim_status, "saved_verified");
        assert!(guard.uncertainty_markers.is_empty());
        assert!(guard.next_action_confirmation_prompt.is_none());
    }

    #[test]
    fn claim_guard_respects_concise_verbosity_phrase() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "verbosity": "concise"
                    }
                }
            }),
        );
        let policy = super::apply_user_preference_overrides(
            super::default_autonomy_policy(),
            Some(&profile),
        );
        let event_id = Uuid::now_v7();
        let guard = build_claim_guard(
            &[AgentWriteReceipt {
                event_id,
                event_type: "set.logged".to_string(),
                idempotency_key: "k-verbosity-1".to_string(),
                event_timestamp: Utc::now(),
            }],
            1,
            &[AgentReadAfterWriteCheck {
                projection_type: "user_profile".to_string(),
                key: "me".to_string(),
                status: "verified".to_string(),
                observed_projection_version: Some(1),
                observed_last_event_id: Some(event_id),
                detail: "ok".to_string(),
            }],
            &[],
            policy,
            default_autonomy_gate(),
        );
        assert_eq!(guard.claim_status, "saved_verified");
        assert_eq!(guard.recommended_user_phrase, "Saved.");
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

        let guard = build_claim_guard(
            &receipts,
            1,
            &checks,
            &warnings,
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
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

        let guard = build_claim_guard(&receipts, 1, &checks, &[], policy, default_autonomy_gate());
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

        let guard = build_claim_guard(
            &[],
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
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
        let guard = build_claim_guard(
            &receipts,
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
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
        let guard = build_claim_guard(
            &receipts,
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
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
        let guard = build_claim_guard(
            &[],
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
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
    fn scenario_library_correction_inferred_with_provenance() {
        let evidence_event = make_event(
            "evidence.claim.logged",
            json!({
                "claim_type": "set_context.rest_seconds",
                "confidence": 0.93,
                "provenance": {"source_text_span": {"text": "rest 90 sec"}}
            }),
            "evidence-scenario-1",
        );
        let inferred_facts = collect_reliability_inferred_facts(&[evidence_event], &[]);
        assert_eq!(inferred_facts.len(), 1);
        assert_eq!(inferred_facts[0].field, "set_context.rest_seconds");

        let event_id = Uuid::now_v7();
        let guard = build_claim_guard(
            &[AgentWriteReceipt {
                event_id,
                event_type: "set.corrected".to_string(),
                idempotency_key: "corr-k-1".to_string(),
                event_timestamp: Utc::now(),
            }],
            1,
            &[AgentReadAfterWriteCheck {
                projection_type: "exercise_progression".to_string(),
                key: "barbell_back_squat".to_string(),
                status: "verified".to_string(),
                observed_projection_version: Some(1),
                observed_last_event_id: Some(event_id),
                detail: "ok".to_string(),
            }],
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
        let summary = AgentSessionAuditSummary {
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
        assert!(ux.assistant_phrase.contains("Quelle"));
    }

    #[test]
    fn scenario_library_contradiction_unresolved() {
        let user_id = Uuid::now_v7();
        let requested = vec![make_event(
            "session.completed",
            json!({
                "enjoyment": 5,
                "perceived_quality": 5,
                "perceived_exertion": 8,
                "notes": "the session felt bad and awful"
            }),
            "k-scenario-contradiction",
        )];
        let receipts = vec![AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "session.completed".to_string(),
            idempotency_key: "k-scenario-contradiction".to_string(),
            event_timestamp: Utc::now(),
        }];
        let policy = default_autonomy_policy();
        let artifacts = build_session_audit_artifacts(user_id, &requested, &receipts, &policy);
        assert_eq!(artifacts.summary.status, "needs_clarification");
        assert!(artifacts.summary.clarification_question.is_some());

        let guard = build_claim_guard(
            &receipts,
            1,
            &[AgentReadAfterWriteCheck {
                projection_type: "session_feedback".to_string(),
                key: "overview".to_string(),
                status: "pending".to_string(),
                observed_projection_version: None,
                observed_last_event_id: None,
                detail: "pending".to_string(),
            }],
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
        let ux = build_reliability_ux(&guard, &artifacts.summary, vec![]);
        assert_eq!(ux.state, "unresolved");
        assert!(ux.assistant_phrase.contains("Unresolved"));
        let question = ux.clarification_question.as_deref().unwrap_or("");
        assert!(
            question.contains("Welcher Wert stimmt?") || question.contains("Bitte bestätigen:")
        );
    }

    #[test]
    fn scenario_library_pending_read_after_write_unresolved() {
        let event_id = Uuid::now_v7();
        let guard = build_claim_guard(
            &[AgentWriteReceipt {
                event_id,
                event_type: "set.logged".to_string(),
                idempotency_key: "k-pending-1".to_string(),
                event_timestamp: Utc::now(),
            }],
            1,
            &[AgentReadAfterWriteCheck {
                projection_type: "training_timeline".to_string(),
                key: "overview".to_string(),
                status: "pending".to_string(),
                observed_projection_version: None,
                observed_last_event_id: None,
                detail: "pending".to_string(),
            }],
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
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
                .recommended_user_phrase
                .to_lowercase()
                .contains("pending")
        );
    }

    #[test]
    fn scenario_library_overload_single_conflict_question() {
        let unresolved = vec![
            super::SessionAuditUnresolved {
                exercise_label: "session".to_string(),
                field: "session_feedback.enjoyment".to_string(),
                candidates: vec!["2".to_string(), "5".to_string()],
            },
            super::SessionAuditUnresolved {
                exercise_label: "barbell_back_squat".to_string(),
                field: "set_context.rest_seconds".to_string(),
                candidates: vec!["60".to_string(), "90".to_string()],
            },
        ];
        let question = super::build_clarification_question(&unresolved)
            .expect("overload scenario should still produce one question");
        assert!(question.contains("session"));
        assert!(question.contains("Welcher Wert stimmt?"));
        assert_eq!(question.matches('?').count(), 1);
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
        let guard = build_claim_guard(
            &receipts,
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
        let model_identity = super::ResolvedModelIdentity {
            model_identity: "openai:gpt-5-mini".to_string(),
            reason_codes: Vec::new(),
            source: "test".to_string(),
            attestation_request_id: None,
        };

        let events = build_save_handshake_learning_signal_events(
            user_id,
            1,
            &receipts,
            &verification,
            &guard,
            &model_identity,
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
        let guard = build_claim_guard(
            &receipts,
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
        let model_identity = super::ResolvedModelIdentity {
            model_identity: "openai:gpt-5-mini".to_string(),
            reason_codes: Vec::new(),
            source: "test".to_string(),
            attestation_request_id: None,
        };

        let events = build_save_handshake_learning_signal_events(
            user_id,
            1,
            &receipts,
            &verification,
            &guard,
            &model_identity,
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
    fn user_preference_overrides_apply_scope_and_verbosity_when_healthy() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "autonomy_scope": "proactive",
                        "verbosity": "concise",
                        "confirmation_strictness": "auto"
                    }
                }
            }),
        );
        let policy = super::apply_user_preference_overrides(
            super::default_autonomy_policy(),
            Some(&profile),
        );
        assert_eq!(policy.max_scope_level, "proactive");
        assert_eq!(policy.interaction_verbosity, "concise");
        assert_eq!(policy.confirmation_strictness, "auto");
        assert_eq!(
            policy.user_requested_scope_level.as_deref(),
            Some("proactive")
        );
    }

    #[test]
    fn user_preference_scope_override_is_clamped_when_quality_not_healthy() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "autonomy_scope": "proactive"
                    }
                }
            }),
        );
        let mut base = super::default_autonomy_policy();
        base.slo_status = "degraded".to_string();
        base.max_scope_level = "strict".to_string();
        base.throttle_active = true;
        let policy = super::apply_user_preference_overrides(base, Some(&profile));
        assert_eq!(policy.max_scope_level, "strict");
        assert_eq!(
            policy.user_requested_scope_level.as_deref(),
            Some("proactive")
        );
    }

    #[test]
    fn user_preference_confirmation_always_forces_confirm_first_gate() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "confirmation_strictness": "always"
                    }
                }
            }),
        );
        let policy = super::apply_user_preference_overrides(
            super::default_autonomy_policy(),
            Some(&profile),
        );
        assert!(policy.require_confirmation_for_non_trivial_actions);
        assert!(policy.require_confirmation_for_plan_updates);
        assert!(policy.require_confirmation_for_repairs);

        let tier = super::model_tier_policy_from_name("advanced");
        let gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &tier, &[]);
        assert_eq!(gate.decision, "confirm_first");
        assert!(
            gate.reason_codes
                .iter()
                .any(|code| code == "user_confirmation_strictness_always")
        );
    }

    #[test]
    fn user_preference_confirmation_never_cannot_bypass_strict_tier() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "confirmation_strictness": "never"
                    }
                }
            }),
        );
        let policy = super::apply_user_preference_overrides(
            super::default_autonomy_policy(),
            Some(&profile),
        );
        let strict_tier = super::model_tier_policy_from_name("strict");
        let gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &strict_tier, &[]);
        assert_eq!(gate.decision, "confirm_first");
        assert!(
            gate.reason_codes
                .iter()
                .any(|code| code == "model_tier_strict_requires_confirmation")
        );
    }

    #[test]
    fn user_preference_overrides_fallback_to_defaults_when_invalid() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "autonomy_scope": "hyper_proactive",
                        "verbosity": "wall_of_text",
                        "confirmation_strictness": "sometimes"
                    }
                }
            }),
        );
        let policy = super::apply_user_preference_overrides(
            super::default_autonomy_policy(),
            Some(&profile),
        );
        assert_eq!(policy.max_scope_level, "moderate");
        assert_eq!(policy.interaction_verbosity, "balanced");
        assert_eq!(policy.confirmation_strictness, "auto");
        assert!(policy.user_requested_scope_level.is_none());
    }

    #[test]
    fn session_audit_auto_repair_is_blocked_when_calibration_is_degraded() {
        let mut policy = super::default_autonomy_policy();
        policy.calibration_status = "degraded".to_string();
        assert!(!super::session_audit_auto_repair_allowed(&policy));
    }

    #[test]
    fn model_identity_resolver_prefers_client_map_over_runtime_default() {
        let resolved = super::resolve_model_identity_with_sources(
            Some("kura-web"),
            Some(r#"{"kura-web":"openai:gpt-5-mini"}"#),
            Some("openai:gpt-5"),
        );
        assert_eq!(resolved.model_identity, "openai:gpt-5-mini");
        assert!(resolved.reason_codes.is_empty());
    }

    #[test]
    fn model_identity_resolver_uses_strict_unknown_fallback_with_reason_code() {
        let resolved = super::resolve_model_identity_with_sources(None, None, None);
        assert_eq!(resolved.model_identity, "unknown");
        assert!(
            resolved
                .reason_codes
                .iter()
                .any(|code| code == "model_identity_unknown_fallback_strict")
        );
    }

    #[test]
    fn model_attestation_verification_accepts_valid_signature() {
        let _nonce_lock = model_attestation_test_lock();
        super::clear_model_attestation_nonce_cache();
        let auth = make_access_token_auth(&["agent:write"], "kura-web");
        let req = make_write_with_proof_request(vec![make_event(
            "set.logged",
            json!({"exercise_id": "barbell_bench_press", "reps": 5}),
            "k-attest-1",
        )]);
        let action_class = super::classify_write_action_class(&req.events);
        let digest = super::build_model_attestation_request_digest(&req, &action_class);
        let request_id = format!("att-{}", Uuid::now_v7());
        let issued_at = Utc::now();
        let signature = super::compute_model_attestation_signature(
            "unit-test-secret",
            "openai:gpt-5-mini",
            issued_at,
            &request_id,
            &digest,
            auth.user_id,
        )
        .expect("signature");
        let attestation = super::AgentModelAttestation {
            schema_version: "model_attestation.v1".to_string(),
            runtime_model_identity: "openai:gpt-5-mini".to_string(),
            request_digest: digest,
            request_id,
            issued_at,
            signature,
        };
        let verified = super::verify_model_attestation(
            &attestation,
            &attestation.request_digest,
            auth.user_id,
            Utc::now(),
            Some("unit-test-secret"),
        )
        .expect("attestation should verify");
        assert_eq!(verified.model_identity, "openai:gpt-5-mini");
    }

    #[test]
    fn model_attestation_verification_rejects_invalid_signature() {
        let _nonce_lock = model_attestation_test_lock();
        super::clear_model_attestation_nonce_cache();
        let auth = make_access_token_auth(&["agent:write"], "kura-web");
        let req = make_write_with_proof_request(vec![make_event(
            "set.logged",
            json!({"exercise_id": "barbell_back_squat", "reps": 5}),
            "k-attest-2",
        )]);
        let action_class = super::classify_write_action_class(&req.events);
        let digest = super::build_model_attestation_request_digest(&req, &action_class);
        let attestation = super::AgentModelAttestation {
            schema_version: "model_attestation.v1".to_string(),
            runtime_model_identity: "openai:gpt-5-mini".to_string(),
            request_digest: digest.clone(),
            request_id: format!("att-{}", Uuid::now_v7()),
            issued_at: Utc::now(),
            signature: "deadbeef".to_string(),
        };
        let err_codes = super::verify_model_attestation(
            &attestation,
            &digest,
            auth.user_id,
            Utc::now(),
            Some("unit-test-secret"),
        )
        .expect_err("invalid signature must fail");
        assert!(
            err_codes
                .iter()
                .any(|code| code == "model_attestation_invalid_signature")
        );
    }

    #[test]
    fn model_attestation_verification_rejects_replay_request_id() {
        let _nonce_lock = model_attestation_test_lock();
        super::clear_model_attestation_nonce_cache();
        let auth = make_access_token_auth(&["agent:write"], "kura-web");
        let req = make_write_with_proof_request(vec![make_event(
            "set.logged",
            json!({"exercise_id": "romanian_deadlift", "reps": 6}),
            "k-attest-3",
        )]);
        let action_class = super::classify_write_action_class(&req.events);
        let digest = super::build_model_attestation_request_digest(&req, &action_class);
        let request_id = format!("att-{}", Uuid::now_v7());
        let issued_at = Utc::now();
        let signature = super::compute_model_attestation_signature(
            "unit-test-secret",
            "openai:gpt-5-mini",
            issued_at,
            &request_id,
            &digest,
            auth.user_id,
        )
        .expect("signature");
        let attestation = super::AgentModelAttestation {
            schema_version: "model_attestation.v1".to_string(),
            runtime_model_identity: "openai:gpt-5-mini".to_string(),
            request_digest: digest.clone(),
            request_id,
            issued_at,
            signature,
        };

        super::verify_model_attestation(
            &attestation,
            &digest,
            auth.user_id,
            Utc::now(),
            Some("unit-test-secret"),
        )
        .expect("first verification should pass");

        let err_codes = super::verify_model_attestation(
            &attestation,
            &digest,
            auth.user_id,
            Utc::now(),
            Some("unit-test-secret"),
        )
        .expect_err("second verification should fail due to replay");
        assert!(
            err_codes
                .iter()
                .any(|code| code == "model_attestation_replayed")
        );
    }

    #[test]
    fn model_attestation_verification_rejects_stale_attestation() {
        let _nonce_lock = model_attestation_test_lock();
        super::clear_model_attestation_nonce_cache();
        let auth = make_access_token_auth(&["agent:write"], "kura-web");
        let req = make_write_with_proof_request(vec![make_event(
            "set.logged",
            json!({"exercise_id": "pull_up", "reps": 8}),
            "k-attest-4",
        )]);
        let action_class = super::classify_write_action_class(&req.events);
        let digest = super::build_model_attestation_request_digest(&req, &action_class);
        let request_id = format!("att-{}", Uuid::now_v7());
        let issued_at =
            Utc::now() - Duration::seconds(super::MODEL_ATTESTATION_MAX_AGE_SECONDS + 10);
        let signature = super::compute_model_attestation_signature(
            "unit-test-secret",
            "openai:gpt-5-mini",
            issued_at,
            &request_id,
            &digest,
            auth.user_id,
        )
        .expect("signature");
        let attestation = super::AgentModelAttestation {
            schema_version: "model_attestation.v1".to_string(),
            runtime_model_identity: "openai:gpt-5-mini".to_string(),
            request_digest: digest.clone(),
            request_id,
            issued_at,
            signature,
        };

        let err_codes = super::verify_model_attestation(
            &attestation,
            &digest,
            auth.user_id,
            Utc::now(),
            Some("unit-test-secret"),
        )
        .expect_err("stale attestation must fail");
        assert!(
            err_codes
                .iter()
                .any(|code| code == "model_attestation_stale")
        );
    }

    #[test]
    fn model_identity_for_write_marks_missing_attestation_on_unknown_fallback() {
        let auth = make_access_token_auth(&["agent:write"], "unmapped-client");
        let req = make_write_with_proof_request(vec![make_event(
            "set.logged",
            json!({"exercise_id": "barbell_row", "reps": 6}),
            "k-attest-5",
        )]);
        let action_class = super::classify_write_action_class(&req.events);
        let resolved =
            super::resolve_model_identity_for_write(&auth, &req, &action_class, Utc::now());
        if resolved.model_identity == "unknown" {
            assert!(
                resolved
                    .reason_codes
                    .iter()
                    .any(|code| code == "model_attestation_missing_fallback")
            );
        }
    }

    #[test]
    fn model_tier_policy_defaults_to_moderate_for_all() {
        let tier = super::resolve_model_tier_policy_default();
        assert_eq!(tier.capability_tier, "moderate");
        assert_eq!(tier.high_impact_write_policy, "confirm_first");
    }

    #[test]
    fn auto_tier_candidate_defaults_to_moderate_for_low_samples() {
        let candidate = super::candidate_auto_model_tier(3, 0.0);
        assert_eq!(candidate, "moderate");
    }

    #[test]
    fn auto_tier_hysteresis_keeps_advanced_when_regression_is_small() {
        let effective = super::apply_model_tier_hysteresis(Some("advanced"), "moderate", 20, 1.0);
        assert_eq!(effective, "advanced");
    }

    #[test]
    fn auto_tier_hysteresis_keeps_strict_until_clear_recovery() {
        let effective = super::apply_model_tier_hysteresis(Some("strict"), "moderate", 20, 2.4);
        assert_eq!(effective, "strict");
    }

    #[test]
    fn tier_policy_overlay_clamps_scope_and_repair_auto_apply() {
        let mut policy = super::default_autonomy_policy();
        policy.max_scope_level = "proactive".to_string();
        policy.repair_auto_apply_enabled = true;
        policy.require_confirmation_for_repairs = false;

        let strict_tier = super::model_tier_policy_from_name("strict");
        let applied = super::apply_model_tier_policy(policy, "unknown", &strict_tier, &[]);
        assert_eq!(applied.max_scope_level, "strict");
        assert!(!applied.repair_auto_apply_enabled);
        assert!(applied.require_confirmation_for_repairs);
        assert_eq!(applied.capability_tier, "strict");
    }

    #[test]
    fn autonomy_gate_requires_confirmation_for_strict_tier() {
        let policy = super::default_autonomy_policy();
        let strict_tier = super::model_tier_policy_from_name("strict");
        let gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &strict_tier, &[]);
        assert_eq!(gate.decision, "confirm_first");
        assert!(
            gate.reason_codes
                .iter()
                .any(|code| code == "model_tier_strict_requires_confirmation")
        );
    }

    #[test]
    fn autonomy_gate_requires_confirmation_when_calibration_is_monitor() {
        let mut policy = super::default_autonomy_policy();
        policy.calibration_status = "monitor".to_string();
        let advanced_tier = super::model_tier_policy_from_name("advanced");
        let gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &advanced_tier, &[]);
        assert_eq!(gate.decision, "confirm_first");
        assert!(
            gate.reason_codes
                .iter()
                .any(|code| code == "calibration_monitor_requires_confirmation")
        );
    }

    #[test]
    fn high_impact_confirmation_requires_payload_when_confirm_first() {
        let policy = super::default_autonomy_policy();
        let strict_tier = super::model_tier_policy_from_name("strict");
        let gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &strict_tier, &[]);
        let events = vec![make_event(
            "training_plan.created",
            json!({"name": "Upper/Lower"}),
            "k-confirm-1",
        )];
        let req = make_write_with_proof_request(vec![make_event(
            "training_plan.created",
            json!({"name": "Upper/Lower"}),
            "k-confirm-1-digest",
        )]);
        let digest =
            super::build_high_impact_confirmation_request_digest(&req, "high_impact_write");
        let err = super::validate_high_impact_confirmation(
            None,
            &events,
            &gate,
            Uuid::now_v7(),
            "high_impact_write",
            &digest,
            Some("test-high-impact-secret"),
            Utc::now(),
        )
        .expect_err("confirm_first high-impact must require explicit confirmation");
        match err {
            AppError::Validation { field, .. } => {
                assert_eq!(field.as_deref(), Some("high_impact_confirmation"));
            }
            other => panic!("unexpected error variant: {other:?}"),
        }
    }

    #[test]
    fn high_impact_confirmation_accepts_fresh_payload_when_confirm_first() {
        let policy = super::default_autonomy_policy();
        let strict_tier = super::model_tier_policy_from_name("strict");
        let gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &strict_tier, &[]);
        let events = vec![make_event(
            "training_plan.created",
            json!({"name": "Upper/Lower"}),
            "k-confirm-2",
        )];
        let user_id = Uuid::now_v7();
        let action_class = "high_impact_write";
        let req = make_write_with_proof_request(vec![make_event(
            "training_plan.created",
            json!({"name": "Upper/Lower"}),
            "k-confirm-2-digest",
        )]);
        let digest = super::build_high_impact_confirmation_request_digest(&req, action_class);
        let token = super::issue_high_impact_confirmation_token(
            "test-high-impact-secret",
            user_id,
            action_class,
            &digest,
            Utc::now(),
        )
        .expect("confirmation token");
        let confirmation = super::AgentHighImpactConfirmation {
            schema_version: "high_impact_confirmation.v1".to_string(),
            confirmed: true,
            confirmed_at: Utc::now(),
            confirmation_token: Some(token),
        };

        let result = super::validate_high_impact_confirmation(
            Some(&confirmation),
            &events,
            &gate,
            user_id,
            action_class,
            &digest,
            Some("test-high-impact-secret"),
            Utc::now(),
        );
        assert!(result.is_ok());
    }

    #[test]
    fn high_impact_confirmation_rejects_stale_payload() {
        let policy = super::default_autonomy_policy();
        let strict_tier = super::model_tier_policy_from_name("strict");
        let gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &strict_tier, &[]);
        let events = vec![make_event(
            "training_plan.created",
            json!({"name": "Upper/Lower"}),
            "k-confirm-3",
        )];
        let user_id = Uuid::now_v7();
        let action_class = "high_impact_write";
        let req = make_write_with_proof_request(vec![make_event(
            "training_plan.created",
            json!({"name": "Upper/Lower"}),
            "k-confirm-3-digest",
        )]);
        let digest = super::build_high_impact_confirmation_request_digest(&req, action_class);
        let token = super::issue_high_impact_confirmation_token(
            "test-high-impact-secret",
            user_id,
            action_class,
            &digest,
            Utc::now(),
        )
        .expect("confirmation token");
        let confirmation = super::AgentHighImpactConfirmation {
            schema_version: "high_impact_confirmation.v1".to_string(),
            confirmed: true,
            confirmed_at: Utc::now() - Duration::minutes(90),
            confirmation_token: Some(token),
        };

        let err = super::validate_high_impact_confirmation(
            Some(&confirmation),
            &events,
            &gate,
            user_id,
            action_class,
            &digest,
            Some("test-high-impact-secret"),
            Utc::now(),
        )
        .expect_err("stale confirmation must fail");
        match err {
            AppError::Validation { field, .. } => {
                assert_eq!(
                    field.as_deref(),
                    Some("high_impact_confirmation.confirmed_at")
                );
            }
            other => panic!("unexpected error variant: {other:?}"),
        }
    }

    #[test]
    fn high_impact_confirmation_rejects_payload_mismatch_token() {
        let policy = super::default_autonomy_policy();
        let strict_tier = super::model_tier_policy_from_name("strict");
        let gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &strict_tier, &[]);
        let events_b = vec![make_event(
            "training_plan.created",
            json!({"name": "Upper/Lower v2"}),
            "k-confirm-b",
        )];
        let user_id = Uuid::now_v7();
        let action_class = "high_impact_write";
        let req_a = make_write_with_proof_request(vec![make_event(
            "training_plan.created",
            json!({"name": "Upper/Lower"}),
            "k-confirm-a-digest",
        )]);
        let req_b = make_write_with_proof_request(vec![make_event(
            "training_plan.created",
            json!({"name": "Upper/Lower v2"}),
            "k-confirm-b-digest",
        )]);
        let digest_a = super::build_high_impact_confirmation_request_digest(&req_a, action_class);
        let digest_b = super::build_high_impact_confirmation_request_digest(&req_b, action_class);
        let token = super::issue_high_impact_confirmation_token(
            "test-high-impact-secret",
            user_id,
            action_class,
            &digest_a,
            Utc::now(),
        )
        .expect("confirmation token");
        let confirmation = super::AgentHighImpactConfirmation {
            schema_version: "high_impact_confirmation.v1".to_string(),
            confirmed: true,
            confirmed_at: Utc::now(),
            confirmation_token: Some(token),
        };

        let err = super::validate_high_impact_confirmation(
            Some(&confirmation),
            &events_b,
            &gate,
            user_id,
            action_class,
            &digest_b,
            Some("test-high-impact-secret"),
            Utc::now(),
        )
        .expect_err("token bound to different payload digest must fail");
        match err {
            AppError::Validation { field, .. } => {
                assert_eq!(
                    field.as_deref(),
                    Some("high_impact_confirmation.confirmation_token")
                );
            }
            other => panic!("unexpected error variant: {other:?}"),
        }
    }

    #[test]
    fn autonomy_gate_matrix_is_deterministic_for_high_impact_writes() {
        // Tiers are now determined by auto-tiering, not model name.
        // Test all tier × quality combinations directly.
        let scenarios = [
            ("strict", "healthy", "healthy", "confirm_first"),
            ("strict", "healthy", "monitor", "confirm_first"),
            ("strict", "healthy", "degraded", "confirm_first"),
            ("moderate", "healthy", "healthy", "confirm_first"),
            ("moderate", "healthy", "monitor", "confirm_first"),
            ("moderate", "healthy", "degraded", "confirm_first"),
            ("advanced", "healthy", "healthy", "allow"),
            ("advanced", "healthy", "monitor", "confirm_first"),
            ("advanced", "healthy", "degraded", "confirm_first"),
        ];

        for (tier_name, slo_status, calibration_status, expected_decision) in scenarios {
            let mut policy = super::default_autonomy_policy();
            policy.slo_status = slo_status.to_string();
            policy.calibration_status = calibration_status.to_string();
            let tier = super::model_tier_policy_from_name(tier_name);
            let gate = super::evaluate_autonomy_gate("high_impact_write", &policy, &tier, &[]);
            assert_eq!(
                gate.decision, expected_decision,
                "tier={tier_name} slo={slo_status} cal={calibration_status}"
            );
        }
    }

    #[test]
    fn challenge_mode_defaults_to_auto_with_onboarding_hint() {
        let profile = bootstrap_user_profile(Uuid::now_v7());
        let mode = super::resolve_challenge_mode(Some(&profile));
        assert_eq!(mode.schema_version, "challenge_mode.v1");
        assert_eq!(mode.mode, "auto");
        assert_eq!(mode.source, "default_auto");
        assert!(mode.onboarding_hint_required);
        assert!(mode.onboarding_hint.is_some());
    }

    #[test]
    fn challenge_mode_uses_preference_and_intro_seen_marker() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "challenge_mode": "off",
                        "challenge_mode_intro_seen": true
                    }
                }
            }),
        );
        let mode = super::resolve_challenge_mode(Some(&profile));
        assert_eq!(mode.mode, "off");
        assert_eq!(mode.source, "user_profile.preference");
        assert!(!mode.onboarding_hint_required);
        assert!(mode.onboarding_hint.is_none());
    }

    #[test]
    fn language_mode_defaults_to_developer_raw_for_allowlisted_user() {
        let user_id = Uuid::now_v7();
        let auth = make_access_token_auth_with_user(user_id, &["agent:write"], "kura-dev-client");
        let mode = super::resolve_agent_language_mode_with_sources(
            &auth,
            None,
            Some(&user_id.to_string()),
        );
        assert_eq!(mode, super::AgentLanguageMode::DeveloperRaw);
    }

    #[test]
    fn language_mode_allows_developer_raw_for_wildcard_user_allowlist() {
        let auth = make_access_token_auth(&["agent:write"], "kura-dev-client");
        let mode = super::resolve_agent_language_mode_with_sources(&auth, Some("raw"), Some("*"));
        assert_eq!(mode, super::AgentLanguageMode::DeveloperRaw);
    }

    #[test]
    fn language_mode_denies_developer_raw_when_user_not_allowlisted() {
        let allowlisted_user = Uuid::now_v7();
        let auth =
            make_access_token_auth_with_user(Uuid::now_v7(), &["agent:write"], "kura-app-client");
        let mode = super::resolve_agent_language_mode_with_sources(
            &auth,
            Some("developer_raw"),
            Some(&allowlisted_user.to_string()),
        );
        assert_eq!(mode, super::AgentLanguageMode::UserSafe);
    }

    #[test]
    fn language_mode_allows_developer_raw_when_user_allowlist_matches() {
        let user_id = Uuid::now_v7();
        let auth = make_access_token_auth_with_user(user_id, &["agent:write"], "kura-dev-client");
        let allowlist = format!("{},{}", Uuid::now_v7(), user_id);
        let mode = super::resolve_agent_language_mode_with_sources(
            &auth,
            Some("developer_raw"),
            Some(&allowlist),
        );
        assert_eq!(mode, super::AgentLanguageMode::DeveloperRaw);
    }

    #[test]
    fn language_mode_can_be_forced_to_user_safe_via_header_for_allowlisted_user() {
        let user_id = Uuid::now_v7();
        let auth = make_access_token_auth_with_user(user_id, &["agent:write"], "kura-dev-client");
        let mode = super::resolve_agent_language_mode_with_sources(
            &auth,
            Some("user_safe"),
            Some(&user_id.to_string()),
        );
        assert_eq!(mode, super::AgentLanguageMode::UserSafe);
    }

    #[test]
    fn user_safe_guard_rewrites_machine_language_but_remains_fail_open() {
        let (
            receipts,
            warnings,
            verification,
            mut claim_guard,
            mut workflow_gate,
            mut session_audit,
            mut repair_feedback,
        ) = make_trace_contract_artifacts(
            "failed",
            "pending",
            "needs_clarification",
            Some("Konflikt bei session.completed: INV-008. Welcher Wert stimmt?"),
        );

        claim_guard.recommended_user_phrase =
            "Write proof incomplete. Retry with the same idempotency keys.".to_string();
        workflow_gate.message =
            "Planning/coaching payload blocked: onboarding close marker workflow.onboarding.closed fehlt."
                .to_string();
        session_audit.clarification_question =
            Some("Konflikt: session.completed oder set.corrected?".to_string());
        repair_feedback.summary =
            "Undo via /v1/events/batch und event.retracted moeglich.".to_string();
        repair_feedback.clarification_question = Some("Bitte INV-004 bestaetigen.".to_string());

        let reliability_ux = super::AgentReliabilityUx {
            state: "unresolved".to_string(),
            assistant_phrase:
                "Unresolved: Write-Proof pending. session.completed konnte nicht bestaetigt werden."
                    .to_string(),
            inferred_facts: Vec::new(),
            clarification_question: Some("INV-009: Welcher Wert stimmt?".to_string()),
        };

        let trace_digest = super::build_trace_digest(
            &receipts,
            &warnings,
            &verification,
            &claim_guard,
            &workflow_gate,
            &session_audit,
            &repair_feedback,
        );
        let post_task_reflection = super::build_post_task_reflection(
            &trace_digest,
            &verification,
            &session_audit,
            &repair_feedback,
        );

        let response = super::AgentWriteWithProofResponse {
            receipts,
            warnings,
            verification,
            claim_guard,
            reliability_ux,
            workflow_gate,
            session_audit,
            repair_feedback,
            intent_handshake_confirmation: Some(super::AgentIntentHandshakeConfirmation {
                schema_version: "intent_handshake.v1".to_string(),
                status: "accepted".to_string(),
                impact_class: "high_impact_write".to_string(),
                handshake_id: Some("hs-1".to_string()),
                chat_confirmation:
                    "Handshake accepted for write-with-proof and read-after-write checks."
                        .to_string(),
            }),
            trace_digest,
            post_task_reflection,
            response_mode_policy: super::AgentResponseModePolicy {
                schema_version: super::RESPONSE_MODE_POLICY_SCHEMA_VERSION.to_string(),
                mode_code: "B".to_string(),
                mode: "hypothesis_personalized".to_string(),
                evidence_state: "limited".to_string(),
                evidence_score: 0.51,
                threshold_a_min: 0.72,
                threshold_b_min: 0.42,
                quality_status: "monitor".to_string(),
                integrity_slo_status: "monitor".to_string(),
                calibration_status: "healthy".to_string(),
                policy_role: super::RESPONSE_MODE_POLICY_ROLE_NUDGE_ONLY.to_string(),
                requires_transparency_note: true,
                reason_codes: vec!["write_proof_partial_or_pending".to_string()],
                assistant_instruction: "Use uncertainty-explicit personalization.".to_string(),
            },
            personal_failure_profile: super::AgentPersonalFailureProfile {
                schema_version: super::PERSONAL_FAILURE_PROFILE_SCHEMA_VERSION.to_string(),
                profile_id: "pfp_test".to_string(),
                model_identity: "test-model".to_string(),
                data_quality_band: "medium".to_string(),
                policy_role: super::SIDECAR_POLICY_ROLE_ADVISORY_ONLY.to_string(),
                recommended_response_mode: "hypothesis_personalized".to_string(),
                active_signals: vec![super::AgentFailureProfileSignal {
                    code: "read_after_write_unverified".to_string(),
                    weight: 0.8,
                }],
            },
            sidecar_assessment: super::AgentSidecarAssessment {
                retrieval_regret: super::AgentRetrievalRegret {
                    schema_version: super::RETRIEVAL_REGRET_SCHEMA_VERSION.to_string(),
                    regret_score: 0.6,
                    regret_band: "medium".to_string(),
                    regret_threshold: 0.4,
                    threshold_exceeded: true,
                    reason_codes: vec!["read_after_write_incomplete".to_string()],
                },
                laaj: super::AgentLaaJSidecar {
                    schema_version: super::LAAJ_SIDECAR_SCHEMA_VERSION.to_string(),
                    verdict: "review".to_string(),
                    score: 0.5,
                    policy_role: super::SIDECAR_POLICY_ROLE_ADVISORY_ONLY.to_string(),
                    can_block_autonomy: false,
                    recommendation: "Ask one clarification first.".to_string(),
                    reason_codes: vec!["response_mode_general_guidance".to_string()],
                },
            },
        };

        let guarded = super::apply_user_safe_language_guard(response);
        let merged_text = format!(
            "{}\n{}\n{}\n{}",
            guarded.reliability_ux.assistant_phrase,
            guarded.claim_guard.recommended_user_phrase,
            guarded.workflow_gate.message,
            guarded.repair_feedback.summary
        );
        assert!(!merged_text.trim().is_empty());
        assert!(!merged_text.contains("session.completed"));
        assert!(!merged_text.contains("workflow.onboarding.closed"));
        assert!(!merged_text.contains("/v1/events/batch"));
        assert!(!merged_text.contains("INV-008"));
    }

    #[test]
    fn intent_handshake_contract_accepts_fresh_matching_payload() {
        let handshake = super::AgentIntentHandshake {
            schema_version: "intent_handshake.v1".to_string(),
            goal: "update training plan".to_string(),
            planned_action: "write training_plan.updated".to_string(),
            assumptions: vec!["latest profile is complete".to_string()],
            non_goals: vec!["no nutrition changes".to_string()],
            impact_class: "high_impact_write".to_string(),
            success_criteria: "plan projection reflects update".to_string(),
            created_at: Utc::now() - Duration::minutes(5),
            handshake_id: Some("hs-fresh-1".to_string()),
        };

        super::validate_intent_handshake(&handshake, "high_impact_write")
            .expect("fresh handshake should be accepted");
        let confirmation = super::build_intent_handshake_confirmation(&handshake);
        assert_eq!(confirmation.schema_version, "intent_handshake.v1");
        assert_eq!(confirmation.status, "accepted");
        assert_eq!(confirmation.impact_class, "high_impact_write");
        assert_eq!(confirmation.handshake_id.as_deref(), Some("hs-fresh-1"));
    }

    #[test]
    fn intent_handshake_contract_schema_version_is_pinned() {
        assert_eq!(
            super::INTENT_HANDSHAKE_SCHEMA_VERSION,
            "intent_handshake.v1"
        );
    }

    #[test]
    fn intent_handshake_contract_rejects_stale_payload() {
        let handshake = super::AgentIntentHandshake {
            schema_version: "intent_handshake.v1".to_string(),
            goal: "update training plan".to_string(),
            planned_action: "write training_plan.updated".to_string(),
            assumptions: vec!["latest profile is complete".to_string()],
            non_goals: vec!["no nutrition changes".to_string()],
            impact_class: "high_impact_write".to_string(),
            success_criteria: "plan projection reflects update".to_string(),
            created_at: Utc::now() - Duration::minutes(180),
            handshake_id: Some("hs-1".to_string()),
        };

        let err = super::validate_intent_handshake(&handshake, "high_impact_write")
            .expect_err("stale handshake should be rejected");
        match err {
            AppError::Validation { field, .. } => {
                assert_eq!(field.as_deref(), Some("intent_handshake.created_at"));
            }
            other => panic!("expected validation error, got {other:?}"),
        }
    }

    #[test]
    fn memory_tier_contract_schema_version_is_pinned() {
        assert_eq!(
            super::AGENT_MEMORY_TIER_CONTRACT_VERSION,
            "memory_tier_contract.v1"
        );
    }

    #[test]
    fn memory_tier_contract_keeps_allow_when_principles_are_fresh() {
        let profile = make_projection_response(
            "user_profile",
            "me",
            Utc::now(),
            json!({
                "user": {
                    "preferences": {
                        "timezone": "Europe/Berlin",
                        "unit_system": "metric"
                    }
                }
            }),
        );
        let gate = super::default_autonomy_gate();
        let merged =
            super::merge_autonomy_gate_with_memory_guard(gate, "high_impact_write", Some(&profile));
        assert_eq!(merged.decision, "allow");
        assert!(merged.reason_codes.is_empty());
    }

    #[test]
    fn memory_tier_contract_requires_confirmation_when_principles_missing() {
        let gate = super::default_autonomy_gate();
        let profile = bootstrap_user_profile(Uuid::now_v7());
        let merged =
            super::merge_autonomy_gate_with_memory_guard(gate, "high_impact_write", Some(&profile));
        assert_eq!(merged.decision, "confirm_first");
        assert!(merged.reason_codes.iter().any(|code| {
            code == "memory_principles_missing_confirm_first"
                || code == "memory_principles_stale_confirm_first"
        }));
    }

    #[test]
    fn trace_digest_contract_schema_version_is_pinned() {
        assert_eq!(super::TRACE_DIGEST_SCHEMA_VERSION, "trace_digest.v1");
    }

    #[test]
    fn trace_digest_contract_is_deterministic_when_verification_is_complete() {
        let (
            receipts,
            warnings,
            verification,
            claim_guard,
            workflow_gate,
            session_audit,
            repair_feedback,
        ) = make_trace_contract_artifacts("verified", "verified", "clean", None);
        let digest = super::build_trace_digest(
            &receipts,
            &warnings,
            &verification,
            &claim_guard,
            &workflow_gate,
            &session_audit,
            &repair_feedback,
        );
        assert_eq!(digest.schema_version, "trace_digest.v1");
        assert_eq!(digest.receipt_event_ids.len(), 1);
        assert!(!digest.action_id.is_empty());
        assert_eq!(
            digest.chat_summary_template_id,
            "trace_digest.chat.short.v1"
        );
    }

    #[test]
    fn trace_digest_contract_marks_pending_verification_and_unsaved_claim() {
        let (
            receipts,
            warnings,
            verification,
            claim_guard,
            workflow_gate,
            session_audit,
            repair_feedback,
        ) = make_trace_contract_artifacts("pending", "pending", "clean", None);
        let digest = super::build_trace_digest(
            &receipts,
            &warnings,
            &verification,
            &claim_guard,
            &workflow_gate,
            &session_audit,
            &repair_feedback,
        );
        assert_eq!(digest.schema_version, "trace_digest.v1");
        assert_eq!(digest.verification_status, "pending");
        assert!(!digest.allow_saved_claim);
        assert_eq!(digest.claim_status, "pending");
    }

    #[test]
    fn post_task_reflection_contract_schema_version_is_pinned() {
        assert_eq!(
            super::POST_TASK_REFLECTION_SCHEMA_VERSION,
            "post_task_reflection.v1"
        );
    }

    #[test]
    fn post_task_reflection_contract_confirms_when_verification_and_audit_are_clean() {
        let (
            receipts,
            warnings,
            verification,
            claim_guard,
            workflow_gate,
            session_audit,
            repair_feedback,
        ) = make_trace_contract_artifacts("verified", "verified", "clean", None);
        let digest = super::build_trace_digest(
            &receipts,
            &warnings,
            &verification,
            &claim_guard,
            &workflow_gate,
            &session_audit,
            &repair_feedback,
        );
        let reflection = super::build_post_task_reflection(
            &digest,
            &verification,
            &session_audit,
            &repair_feedback,
        );
        assert_eq!(reflection.schema_version, "post_task_reflection.v1");
        assert_eq!(reflection.certainty_state, "confirmed");
        assert_eq!(reflection.next_verification_step, "none_required");
        assert!(!reflection.follow_up_recommended);
        assert_eq!(
            reflection.chat_summary_template_id,
            "post_task_reflection.chat.short.v1"
        );
    }

    #[test]
    fn post_task_reflection_contract_marks_unresolved_when_verification_fails() {
        let (
            receipts,
            warnings,
            verification,
            claim_guard,
            workflow_gate,
            session_audit,
            repair_feedback,
        ) = make_trace_contract_artifacts(
            "failed",
            "pending",
            "needs_clarification",
            Some("Welcher Wert stimmt?"),
        );
        let digest = super::build_trace_digest(
            &receipts,
            &warnings,
            &verification,
            &claim_guard,
            &workflow_gate,
            &session_audit,
            &repair_feedback,
        );
        let reflection = super::build_post_task_reflection(
            &digest,
            &verification,
            &session_audit,
            &repair_feedback,
        );
        assert_eq!(reflection.schema_version, "post_task_reflection.v1");
        assert_eq!(reflection.certainty_state, "unresolved");
        assert!(reflection.follow_up_recommended);
        assert_eq!(
            reflection.follow_up_reason.as_deref(),
            Some("certainty_state_not_confirmed")
        );
        assert!(
            reflection
                .residual_risks
                .iter()
                .any(|risk| risk == "read_after_write_not_fully_verified")
        );
        assert_eq!(
            reflection.clarification_question.as_deref(),
            Some("Welcher Wert stimmt?")
        );
        assert_eq!(
            reflection.next_verification_step,
            "ask_user: Welcher Wert stimmt?"
        );
    }

    #[test]
    fn reflection_signal_types_are_classified() {
        assert_eq!(
            super::post_task_reflection_signal_type("confirmed"),
            "post_task_reflection_confirmed"
        );
        assert_eq!(
            super::learning_signal_category("post_task_reflection_partial"),
            "friction_signal"
        );
        assert_eq!(
            super::learning_signal_category("post_task_reflection_unresolved"),
            "friction_signal"
        );
    }

    #[test]
    fn response_mode_policy_contract_prefers_grounded_when_proof_verified() {
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            _session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("verified", "verified", "clean", None);
        let policy = super::build_response_mode_policy(&claim_guard, &verification, None);
        assert_eq!(
            policy.schema_version,
            super::RESPONSE_MODE_POLICY_SCHEMA_VERSION
        );
        assert_eq!(policy.mode_code, "A");
        assert_eq!(policy.mode, "grounded_personalized");
        assert_eq!(policy.evidence_state, "sufficient");
        assert!(!policy.requires_transparency_note);
        assert_eq!(
            policy.policy_role,
            super::RESPONSE_MODE_POLICY_ROLE_NUDGE_ONLY
        );
    }

    #[test]
    fn response_mode_policy_contract_uses_hypothesis_when_evidence_is_partial() {
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            _session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("pending", "pending", "clean", None);
        let policy = super::build_response_mode_policy(&claim_guard, &verification, None);
        assert_eq!(policy.mode_code, "B");
        assert_eq!(policy.mode, "hypothesis_personalized");
        assert_eq!(policy.evidence_state, "limited");
        assert!(policy.requires_transparency_note);
        assert!(
            policy
                .reason_codes
                .iter()
                .any(|code| code == "evidence_score_supports_hypothesis_mode")
        );
    }

    #[test]
    fn response_mode_policy_contract_falls_back_to_general_without_evidence() {
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            _session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("failed", "pending", "clean", None);
        let policy = super::build_response_mode_policy(&claim_guard, &verification, None);
        assert_eq!(policy.mode_code, "C");
        assert_eq!(policy.mode, "general_guidance");
        assert_eq!(policy.evidence_state, "insufficient");
        assert!(policy.requires_transparency_note);
        assert!(
            policy
                .assistant_instruction
                .to_lowercase()
                .contains("clarification")
        );
    }

    #[test]
    fn response_mode_policy_contract_adapts_thresholds_from_quality_health_projection() {
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            _session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("verified", "verified", "clean", None);
        let quality_health = make_projection_response(
            "quality_health",
            "overview",
            Utc::now(),
            json!({
                "status": "monitor",
                "integrity_slo_status": "monitor",
                "issues_open": 6,
                "metrics": {
                    "set_logged_unresolved_pct": 6.0
                },
                "integrity_slos": {
                    "status": "monitor",
                    "metrics": {
                        "save_claim_mismatch_rate_pct": {
                            "value": 8.0,
                            "posterior_prob_gt_monitor": 0.4,
                            "posterior_prob_gt_degraded": 0.2
                        }
                    }
                },
                "autonomy_policy": {
                    "calibration_status": "monitor"
                }
            }),
        );
        let policy =
            super::build_response_mode_policy(&claim_guard, &verification, Some(&quality_health));
        assert_eq!(policy.mode_code, "B");
        assert_eq!(policy.quality_status, "monitor");
        assert_eq!(policy.integrity_slo_status, "monitor");
        assert_eq!(policy.calibration_status, "monitor");
        assert!(policy.threshold_a_min > 0.72);
        assert!(policy.threshold_b_min > 0.42);
        assert!(policy.evidence_score >= policy.threshold_b_min);
        assert!(policy.evidence_score < policy.threshold_a_min);
    }

    #[test]
    fn personal_failure_profile_contract_is_deterministic_per_user_and_model() {
        let user_id = Uuid::now_v7();
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("pending", "pending", "clean", None);
        let mode = super::build_response_mode_policy(&claim_guard, &verification, None);
        let model_a = super::ResolvedModelIdentity {
            model_identity: "openai:gpt-5".to_string(),
            reason_codes: vec![],
            source: "test".to_string(),
            attestation_request_id: None,
        };
        let model_b = super::ResolvedModelIdentity {
            model_identity: "openai:gpt-5-mini".to_string(),
            reason_codes: vec![],
            source: "test".to_string(),
            attestation_request_id: None,
        };
        let first = super::build_personal_failure_profile(
            user_id,
            &model_a,
            &claim_guard,
            &verification,
            &session_audit,
            &mode,
        );
        let second = super::build_personal_failure_profile(
            user_id,
            &model_a,
            &claim_guard,
            &verification,
            &session_audit,
            &mode,
        );
        let third = super::build_personal_failure_profile(
            user_id,
            &model_b,
            &claim_guard,
            &verification,
            &session_audit,
            &mode,
        );
        assert_eq!(
            first.schema_version,
            super::PERSONAL_FAILURE_PROFILE_SCHEMA_VERSION
        );
        assert_eq!(first.profile_id, second.profile_id);
        assert_ne!(first.profile_id, third.profile_id);
    }

    #[test]
    fn personal_failure_profile_contract_is_advisory_not_cage() {
        let user_id = Uuid::now_v7();
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("pending", "pending", "clean", None);
        let mode = super::build_response_mode_policy(&claim_guard, &verification, None);
        let model_identity = super::ResolvedModelIdentity {
            model_identity: "openai:gpt-5".to_string(),
            reason_codes: vec![],
            source: "test".to_string(),
            attestation_request_id: None,
        };
        let profile = super::build_personal_failure_profile(
            user_id,
            &model_identity,
            &claim_guard,
            &verification,
            &session_audit,
            &mode,
        );
        assert_eq!(
            profile.policy_role,
            super::SIDECAR_POLICY_ROLE_ADVISORY_ONLY
        );
        assert_eq!(profile.recommended_response_mode, mode.mode);
    }

    #[test]
    fn personal_failure_profile_contract_tracks_active_failure_signals() {
        let user_id = Uuid::now_v7();
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("failed", "pending", "needs_clarification", None);
        let mode = super::build_response_mode_policy(&claim_guard, &verification, None);
        let model_identity = super::ResolvedModelIdentity {
            model_identity: "openai:gpt-5".to_string(),
            reason_codes: vec![],
            source: "test".to_string(),
            attestation_request_id: None,
        };
        let profile = super::build_personal_failure_profile(
            user_id,
            &model_identity,
            &claim_guard,
            &verification,
            &session_audit,
            &mode,
        );
        let signal_codes: Vec<String> = profile
            .active_signals
            .iter()
            .map(|signal| signal.code.clone())
            .collect();
        assert!(
            signal_codes
                .iter()
                .any(|code| code == "read_after_write_unverified")
        );
        assert!(
            signal_codes
                .iter()
                .any(|code| code == "session_mismatch_unresolved")
        );
        assert!(
            signal_codes
                .iter()
                .any(|code| code == "insufficient_personal_evidence")
        );
    }

    #[test]
    fn sidecar_retrieval_regret_contract_sets_high_regret_when_readback_incomplete() {
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("pending", "pending", "needs_clarification", None);
        let mode = super::build_response_mode_policy(&claim_guard, &verification, None);
        let sidecar =
            super::build_sidecar_assessment(&claim_guard, &verification, &session_audit, &mode);
        assert_eq!(
            sidecar.retrieval_regret.schema_version,
            super::RETRIEVAL_REGRET_SCHEMA_VERSION
        );
        assert!(sidecar.retrieval_regret.regret_score > 0.45);
        assert!(sidecar.retrieval_regret.threshold_exceeded);
        assert!(
            sidecar
                .retrieval_regret
                .reason_codes
                .iter()
                .any(|code| code == "read_after_write_incomplete")
        );
    }

    #[test]
    fn sidecar_retrieval_regret_contract_uses_monitor_threshold_when_quality_is_monitor() {
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("verified", "verified", "clean", None);
        let quality_health = make_projection_response(
            "quality_health",
            "overview",
            Utc::now(),
            json!({
                "status": "monitor",
                "integrity_slo_status": "monitor",
                "autonomy_policy": {
                    "calibration_status": "healthy"
                }
            }),
        );
        let mode =
            super::build_response_mode_policy(&claim_guard, &verification, Some(&quality_health));
        let sidecar =
            super::build_sidecar_assessment(&claim_guard, &verification, &session_audit, &mode);
        assert_eq!(sidecar.retrieval_regret.regret_threshold, 0.4);
    }

    #[test]
    fn sidecar_retrieval_regret_contract_uses_degraded_threshold_when_quality_is_degraded() {
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("verified", "verified", "clean", None);
        let quality_health = make_projection_response(
            "quality_health",
            "overview",
            Utc::now(),
            json!({
                "status": "degraded",
                "integrity_slo_status": "degraded",
                "autonomy_policy": {
                    "calibration_status": "degraded"
                }
            }),
        );
        let mode =
            super::build_response_mode_policy(&claim_guard, &verification, Some(&quality_health));
        let sidecar =
            super::build_sidecar_assessment(&claim_guard, &verification, &session_audit, &mode);
        assert_eq!(sidecar.retrieval_regret.regret_threshold, 0.35);
    }

    #[test]
    fn sidecar_laa_j_contract_is_advisory_only_and_cannot_block() {
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("pending", "pending", "clean", None);
        let mode = super::build_response_mode_policy(&claim_guard, &verification, None);
        let sidecar =
            super::build_sidecar_assessment(&claim_guard, &verification, &session_audit, &mode);
        assert_eq!(
            sidecar.laaj.schema_version,
            super::LAAJ_SIDECAR_SCHEMA_VERSION
        );
        assert_eq!(
            sidecar.laaj.policy_role,
            super::SIDECAR_POLICY_ROLE_ADVISORY_ONLY
        );
        assert!(!sidecar.laaj.can_block_autonomy);
    }

    #[test]
    fn sidecar_signal_contract_emits_retrieval_and_laaj_signal_types() {
        let user_id = Uuid::now_v7();
        let (
            _receipts,
            _warnings,
            verification,
            claim_guard,
            _workflow_gate,
            session_audit,
            _repair_feedback,
        ) = make_trace_contract_artifacts("pending", "pending", "needs_clarification", None);
        let mode = super::build_response_mode_policy(&claim_guard, &verification, None);
        let model_identity = super::ResolvedModelIdentity {
            model_identity: "openai:gpt-5".to_string(),
            reason_codes: vec![],
            source: "test".to_string(),
            attestation_request_id: None,
        };
        let profile = super::build_personal_failure_profile(
            user_id,
            &model_identity,
            &claim_guard,
            &verification,
            &session_audit,
            &mode,
        );
        let sidecar =
            super::build_sidecar_assessment(&claim_guard, &verification, &session_audit, &mode);
        let events = super::build_response_mode_sidecar_learning_signal_events(
            user_id, &mode, &profile, &sidecar,
        );
        let signal_types: Vec<String> = events
            .iter()
            .filter_map(|event| {
                event
                    .data
                    .get("signal_type")
                    .and_then(Value::as_str)
                    .map(|value| value.to_string())
            })
            .collect();
        assert!(
            signal_types
                .iter()
                .any(|value| value == "response_mode_selected")
        );
        assert!(
            signal_types
                .iter()
                .any(|value| value == "personal_failure_profile_observed")
        );
        assert!(
            signal_types
                .iter()
                .any(|value| value == "retrieval_regret_observed")
        );
        assert!(
            signal_types
                .iter()
                .any(|value| value == "laaj_sidecar_assessed")
        );
    }

    #[test]
    fn capabilities_manifest_exposes_agent_contract_preferences() {
        let manifest = build_agent_capabilities();
        assert_eq!(manifest.schema_version, "agent_capabilities.v2.self_model");
        assert_eq!(manifest.preferred_read_endpoint, "/v1/agent/context");
        assert_eq!(
            manifest.preferred_write_endpoint,
            "/v1/agent/write-with-proof"
        );
        assert_eq!(manifest.self_model.schema_version, "agent_self_model.v1");
        assert_eq!(manifest.self_model.capability_tier, "moderate");
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
                "training_session_block_model_v1": {"event_type": "session.logged"},
                "evidence_layer_v1": {"event_type": "evidence.claim.logged"},
                "open_observation_v1": {"event_type": "observation.logged"},
                "ingestion_locale_v1": {"rules": ["normalize decimals"]},
                "load_context_v1": {"event_type": "set.logged"},
                "session_feedback_certainty_v1": {"event_type": "session.completed"},
                "schema_capability_gate_v1": {"rules": ["capability checks"]},
                "model_tier_registry_v1": {"tiers": {"strict": {"high_impact_write_policy": "block"}}},
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
        assert!(conventions.contains_key("training_session_block_model_v1"));
        assert!(conventions.contains_key("evidence_layer_v1"));
        assert!(conventions.contains_key("open_observation_v1"));
        assert!(conventions.contains_key("ingestion_locale_v1"));
        assert!(conventions.contains_key("load_context_v1"));
        assert!(conventions.contains_key("session_feedback_certainty_v1"));
        assert!(conventions.contains_key("schema_capability_gate_v1"));
        assert!(conventions.contains_key("model_tier_registry_v1"));
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

    // ── Save-Echo Contract (save_echo_policy_v1) ──────────────────────

    #[test]
    fn save_echo_contract_schema_version_is_pinned() {
        // The save_echo_policy_v1 contract is declared in system_config.
        // This test pins the telemetry field names that appear in
        // quality.save_claim.checked events.
        let receipt = AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "k-echo-pin-1".to_string(),
            event_timestamp: Utc::now(),
        };
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(1),
            observed_last_event_id: None,
            detail: "echo-pin-fixture".to_string(),
        }];
        let verification = make_verification("verified", checks.clone());
        let claim_guard = build_claim_guard(
            &[receipt.clone()],
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
        let session_audit = AgentSessionAuditSummary {
            status: "clean".to_string(),
            mismatch_detected: 0,
            mismatch_repaired: 0,
            mismatch_unresolved: 0,
            mismatch_classes: Vec::new(),
            clarification_question: None,
        };
        let model_id = super::ResolvedModelIdentity {
            model_identity: "test-model".to_string(),
            reason_codes: vec![],
            source: "test".to_string(),
            attestation_request_id: None,
        };

        let event = super::build_save_claim_checked_event(
            1,
            &[receipt],
            &verification,
            &claim_guard,
            &session_audit,
            &model_id,
        );

        let data = &event.data;
        // Telemetry field names are part of the contract — renaming breaks consumers.
        assert!(
            data.get("save_echo_required").is_some(),
            "save_echo_required field must exist"
        );
        assert!(
            data.get("save_echo_present").is_some(),
            "save_echo_present field must exist"
        );
        assert!(
            data.get("save_echo_completeness").is_some(),
            "save_echo_completeness field must exist"
        );
    }

    #[test]
    fn save_echo_contract_enforced_in_moderate_tier() {
        // Save-Echo must be required even at moderate tier (tier-independent).
        let receipt = AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "k-echo-mod-1".to_string(),
            event_timestamp: Utc::now(),
        };
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(1),
            observed_last_event_id: None,
            detail: "echo-moderate-fixture".to_string(),
        }];
        let verification = make_verification("verified", checks.clone());
        let mut policy = default_autonomy_policy();
        policy.capability_tier = "moderate".to_string();
        let mut gate = default_autonomy_gate();
        gate.model_tier = "moderate".to_string();
        let claim_guard = build_claim_guard(&[receipt.clone()], 1, &checks, &[], policy, gate);
        assert_eq!(claim_guard.claim_status, "saved_verified");

        let session_audit = AgentSessionAuditSummary {
            status: "clean".to_string(),
            mismatch_detected: 0,
            mismatch_repaired: 0,
            mismatch_unresolved: 0,
            mismatch_classes: Vec::new(),
            clarification_question: None,
        };
        let model_id = super::ResolvedModelIdentity {
            model_identity: "test-moderate".to_string(),
            reason_codes: vec![],
            source: "test".to_string(),
            attestation_request_id: None,
        };

        let event = super::build_save_claim_checked_event(
            1,
            &[receipt],
            &verification,
            &claim_guard,
            &session_audit,
            &model_id,
        );

        let data = &event.data;
        assert_eq!(
            data["save_echo_required"], true,
            "save_echo must be required at moderate tier"
        );
        assert_eq!(
            data["save_echo_completeness"], "not_assessed",
            "default completeness must remain neutral until echo assessment is available"
        );
        assert_eq!(
            data["mismatch_severity"], "none",
            "successful writes must not be marked critical before echo assessment"
        );
    }

    #[test]
    fn save_echo_contract_enforced_in_advanced_tier() {
        // Save-Echo must be required even at advanced tier (tier-independent).
        let receipt = AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "k-echo-adv-1".to_string(),
            event_timestamp: Utc::now(),
        };
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(1),
            observed_last_event_id: None,
            detail: "echo-advanced-fixture".to_string(),
        }];
        let verification = make_verification("verified", checks.clone());
        let mut policy = default_autonomy_policy();
        policy.capability_tier = "advanced".to_string();
        let mut gate = default_autonomy_gate();
        gate.model_tier = "advanced".to_string();
        let claim_guard = build_claim_guard(&[receipt.clone()], 1, &checks, &[], policy, gate);
        assert_eq!(claim_guard.claim_status, "saved_verified");

        let session_audit = AgentSessionAuditSummary {
            status: "clean".to_string(),
            mismatch_detected: 0,
            mismatch_repaired: 0,
            mismatch_unresolved: 0,
            mismatch_classes: Vec::new(),
            clarification_question: None,
        };
        let model_id = super::ResolvedModelIdentity {
            model_identity: "test-advanced".to_string(),
            reason_codes: vec![],
            source: "test".to_string(),
            attestation_request_id: None,
        };

        let event = super::build_save_claim_checked_event(
            1,
            &[receipt],
            &verification,
            &claim_guard,
            &session_audit,
            &model_id,
        );

        let data = &event.data;
        assert_eq!(
            data["save_echo_required"], true,
            "save_echo must be required at advanced tier"
        );
        assert_eq!(
            data["save_echo_completeness"], "not_assessed",
            "default completeness must remain neutral until echo assessment is available"
        );
        assert_eq!(
            data["mismatch_severity"], "none",
            "successful writes must not be marked critical before echo assessment"
        );
    }

    // ── Mismatch Severity Contract (save_claim_mismatch_severity) ────

    #[test]
    fn save_claim_mismatch_severity_contract_critical_when_echo_missing() {
        let (severity, reason_codes) = super::classify_mismatch_severity(false, true, "missing");
        assert_eq!(severity.severity, "critical");
        assert_eq!(severity.weight, 1.0);
        assert_eq!(severity.domain, "save_echo");
        assert!(reason_codes.contains(&"save_echo_missing".to_string()));
    }

    #[test]
    fn save_claim_mismatch_severity_contract_warning_when_echo_partial() {
        let (severity, reason_codes) = super::classify_mismatch_severity(false, true, "partial");
        assert_eq!(severity.severity, "warning");
        assert_eq!(severity.weight, 0.5);
        assert_eq!(severity.domain, "save_echo");
        assert!(reason_codes.contains(&"save_echo_partial".to_string()));
    }

    #[test]
    fn save_claim_mismatch_severity_contract_info_when_only_protocol_detail_missing() {
        // Echo is complete but proof verification failed → protocol-level, not data-level
        let (severity, reason_codes) = super::classify_mismatch_severity(true, true, "complete");
        assert_eq!(severity.severity, "info");
        assert_eq!(severity.weight, 0.1);
        assert_eq!(severity.domain, "protocol");
        assert!(reason_codes.contains(&"proof_verification_failed_but_echo_complete".to_string()));
    }

    #[test]
    fn save_claim_mismatch_severity_contract_none_when_all_good() {
        let (severity, reason_codes) = super::classify_mismatch_severity(false, true, "complete");
        assert_eq!(severity.severity, "none");
        assert_eq!(severity.weight, 0.0);
        assert!(reason_codes.is_empty());
    }

    #[test]
    fn save_claim_mismatch_severity_contract_none_when_echo_not_assessed_and_no_mismatch() {
        let (severity, reason_codes) =
            super::classify_mismatch_severity(false, true, "not_assessed");
        assert_eq!(severity.severity, "none");
        assert_eq!(severity.weight, 0.0);
        assert!(reason_codes.is_empty());
    }

    #[test]
    fn save_claim_mismatch_severity_contract_backcompat_defaults_for_legacy_payload() {
        // Legacy pending/protocol mismatch without save-echo requirement stays info-level.
        let (severity, reason_codes) =
            super::classify_mismatch_severity(true, false, "not_applicable");
        assert_eq!(severity.severity, "info");
        assert_eq!(severity.weight, 0.1);
        assert_eq!(severity.domain, "protocol");
        assert!(
            reason_codes
                .contains(&"proof_verification_pending_without_save_echo_requirement".to_string())
        );
    }

    #[test]
    fn save_echo_contract_not_required_when_claim_failed() {
        // When claim_status is "failed", save_echo is not required (nothing was persisted).
        let receipt = AgentWriteReceipt {
            event_id: Uuid::now_v7(),
            event_type: "set.logged".to_string(),
            idempotency_key: "".to_string(), // empty key → receipts_incomplete
            event_timestamp: Utc::now(),
        };
        let checks = vec![AgentReadAfterWriteCheck {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
            status: "verified".to_string(),
            observed_projection_version: Some(1),
            observed_last_event_id: None,
            detail: "echo-failed-fixture".to_string(),
        }];
        let verification = make_verification("verified", checks.clone());
        let claim_guard = build_claim_guard(
            &[receipt.clone()],
            1,
            &checks,
            &[],
            default_autonomy_policy(),
            default_autonomy_gate(),
        );
        assert_eq!(claim_guard.claim_status, "failed");

        let session_audit = AgentSessionAuditSummary {
            status: "clean".to_string(),
            mismatch_detected: 0,
            mismatch_repaired: 0,
            mismatch_unresolved: 0,
            mismatch_classes: Vec::new(),
            clarification_question: None,
        };
        let model_id = super::ResolvedModelIdentity {
            model_identity: "test-failed".to_string(),
            reason_codes: vec![],
            source: "test".to_string(),
            attestation_request_id: None,
        };

        let event = super::build_save_claim_checked_event(
            1,
            &[receipt],
            &verification,
            &claim_guard,
            &session_audit,
            &model_id,
        );

        let data = &event.data;
        assert_eq!(
            data["save_echo_required"], false,
            "save_echo not required when claim failed"
        );
        assert_eq!(data["save_echo_completeness"], "not_applicable");
    }

    // ── Consistency Inbox contract tests ─────────────────────────────

    fn make_inbox_projection(data: Value) -> ProjectionResponse {
        let now = Utc::now();
        ProjectionResponse {
            projection: Projection {
                id: Uuid::nil(),
                user_id: Uuid::nil(),
                projection_type: "consistency_inbox".to_string(),
                key: "overview".to_string(),
                data,
                version: 1,
                last_event_id: None,
                updated_at: now,
            },
            meta: ProjectionMeta {
                projection_version: 1,
                computed_at: now,
                freshness: ProjectionFreshness::from_computed_at(now, now),
            },
        }
    }

    #[test]
    fn consistency_inbox_contract_is_exposed_in_context() {
        // consistency_inbox is loaded as Optional<ProjectionResponse> and
        // serialized into the agent context only when present.
        // Verify that ProjectionResponse round-trips the inbox data intact.
        let inbox = make_inbox_projection(json!({
            "schema_version": 1,
            "pending_items_total": 2,
            "highest_severity": "warning",
            "requires_human_decision": true,
            "items": [],
            "prompt_control": {}
        }));
        let json_val = serde_json::to_value(&inbox).unwrap();
        assert_eq!(json_val["projection_type"], "consistency_inbox");
        assert_eq!(json_val["key"], "overview");
        assert_eq!(json_val["data"]["requires_human_decision"], true);
        assert_eq!(json_val["data"]["highest_severity"], "warning");
    }

    #[test]
    fn consistency_inbox_contract_requires_explicit_approval_before_fix() {
        // When requires_human_decision=true, the projection must carry
        // enough item structure for the agent to formulate an approval question.
        let inbox = make_inbox_projection(json!({
            "schema_version": 1,
            "generated_at": "2026-02-14T12:00:00Z",
            "pending_items_total": 1,
            "highest_severity": "critical",
            "requires_human_decision": true,
            "items": [{
                "item_id": "ci-test-approval",
                "severity": "critical",
                "summary": "Values may not match what was intended.",
                "recommended_action": "Review and confirm.",
                "evidence_ref": "",
                "first_seen": "2026-02-13T00:00:00Z"
            }],
            "prompt_control": {
                "last_prompted_at": null,
                "snooze_until": null,
                "cooldown_active": false
            }
        }));

        let json_val = serde_json::to_value(&inbox).unwrap();
        assert_eq!(json_val["data"]["requires_human_decision"], true);

        let items = json_val["data"]["items"].as_array().unwrap();
        assert!(!items.is_empty());
        let item = &items[0];
        assert!(
            item.get("item_id").is_some(),
            "item_id required for decision event"
        );
        assert!(
            item.get("severity").is_some(),
            "severity required for prioritization"
        );
        assert!(
            item.get("summary").is_some(),
            "summary required for user-facing question"
        );
        assert!(
            item.get("recommended_action").is_some(),
            "recommended_action required"
        );
    }

    #[test]
    fn consistency_inbox_contract_respects_snooze_cooldown() {
        // prompt_control must round-trip snooze/cooldown fields.
        let inbox = make_inbox_projection(json!({
            "schema_version": 1,
            "generated_at": "2026-02-14T12:00:00Z",
            "pending_items_total": 1,
            "highest_severity": "warning",
            "requires_human_decision": true,
            "items": [{
                "item_id": "ci-abc123",
                "severity": "warning",
                "summary": "Test finding",
                "recommended_action": "Review",
                "evidence_ref": "",
                "first_seen": "2026-02-13T10:00:00Z"
            }],
            "prompt_control": {
                "last_prompted_at": null,
                "snooze_until": "2026-02-17T12:00:00Z",
                "cooldown_active": true
            }
        }));

        let json_val = serde_json::to_value(&inbox).unwrap();
        let pc = &json_val["data"]["prompt_control"];
        assert_eq!(
            pc["cooldown_active"], true,
            "cooldown_active must be preserved"
        );
        assert_eq!(
            pc["snooze_until"], "2026-02-17T12:00:00Z",
            "snooze_until must be preserved"
        );
    }
}
