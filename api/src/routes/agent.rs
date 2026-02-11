use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use kura_core::events::{BatchEventWarning, CreateEventRequest, EventMetadata};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::projections::{Projection, ProjectionFreshness, ProjectionMeta, ProjectionResponse};

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::routes::events::create_events_batch_internal;
use crate::routes::system::SystemConfigResponse;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/agent/capabilities", get(get_agent_capabilities))
        .route("/v1/agent/context", get(get_agent_context))
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
pub struct AgentContextMeta {
    pub generated_at: DateTime<Utc>,
    pub exercise_limit: i64,
    pub strength_limit: i64,
    pub custom_limit: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_intent: Option<String>,
    pub ranking_strategy: String,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct AgentContextResponse {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub system: Option<SystemConfigResponse>,
    pub user_profile: ProjectionResponse,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub training_timeline: Option<ProjectionResponse>,
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
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentWriteReceipt {
    pub event_id: Uuid,
    pub event_type: String,
    pub idempotency_key: String,
    pub event_timestamp: DateTime<Utc>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
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
    /// verified | pending
    pub status: String,
    pub checked_at: DateTime<Utc>,
    pub waited_ms: u64,
    pub required_checks: usize,
    pub verified_checks: usize,
    pub checks: Vec<AgentReadAfterWriteCheck>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentWriteClaimGuard {
    pub allow_saved_claim: bool,
    /// verified | deferred
    pub claim_status: String,
    pub uncertainty_markers: Vec<String>,
    pub deferred_markers: Vec<String>,
    pub recommended_user_phrase: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_action_confirmation_prompt: Option<String>,
    pub autonomy_policy: AgentAutonomyPolicy,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AgentWriteWithProofResponse {
    pub receipts: Vec<AgentWriteReceipt>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<BatchEventWarning>,
    pub verification: AgentWriteVerificationSummary,
    pub claim_guard: AgentWriteClaimGuard,
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
            "verified".to_string(),
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
            "verified".to_string(),
            "Saved and verified in the read model.".to_string(),
        )
    } else {
        (
            "deferred".to_string(),
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
) -> CreateEventRequest {
    let mismatch_detected = !claim_guard.allow_saved_claim;
    let event_data = serde_json::json!({
        "requested_event_count": requested_event_count,
        "receipt_count": receipts.len(),
        "allow_saved_claim": claim_guard.allow_saved_claim,
        "claim_status": claim_guard.claim_status,
        "verification_status": verification.status,
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

    let batch_result = create_events_batch_internal(&state, user_id, &req.events).await?;
    let receipts: Vec<AgentWriteReceipt> = batch_result
        .events
        .iter()
        .map(|event| AgentWriteReceipt {
            event_id: event.id,
            event_type: event.event_type.clone(),
            idempotency_key: event.metadata.idempotency_key.clone(),
            event_timestamp: event.timestamp,
        })
        .collect();
    let event_ids: HashSet<Uuid> = receipts.iter().map(|receipt| receipt.event_id).collect();

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
    let verification_status = if verified_checks == checks.len() {
        "verified".to_string()
    } else {
        "pending".to_string()
    };

    let quality_health = fetch_quality_health_projection(&state, user_id).await?;
    let autonomy_policy = autonomy_policy_from_quality_health(quality_health.as_ref());
    let verification = AgentWriteVerificationSummary {
        status: verification_status,
        checked_at: Utc::now(),
        waited_ms,
        required_checks: checks.len(),
        verified_checks,
        checks,
    };
    let claim_guard = build_claim_guard(
        &receipts,
        requested_event_count,
        &verification.checks,
        &batch_result.warnings,
        autonomy_policy,
    );
    let quality_signal = build_save_claim_checked_event(
        requested_event_count,
        &receipts,
        &verification,
        &claim_guard,
    );
    let _ = create_events_batch_internal(&state, user_id, &[quality_signal]).await;

    Ok((
        StatusCode::CREATED,
        Json(AgentWriteWithProofResponse {
            receipts,
            warnings: batch_result.warnings,
            verification,
            claim_guard,
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
    _auth: AuthenticatedUser,
) -> Result<Json<AgentCapabilitiesResponse>, AppError> {
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
    .map(|row| SystemConfigResponse {
        data: row.data,
        version: row.version,
        updated_at: row.updated_at,
    });

    let user_profile = fetch_projection(&mut tx, user_id, "user_profile", "me")
        .await?
        .unwrap_or_else(|| bootstrap_user_profile(user_id));

    let training_timeline =
        fetch_projection(&mut tx, user_id, "training_timeline", "overview").await?;
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
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::{
        AgentReadAfterWriteCheck, AgentReadAfterWriteTarget, AgentWriteReceipt, IntentClass,
        ProjectionResponse, RankingContext, bootstrap_user_profile, build_agent_capabilities,
        build_claim_guard, clamp_limit, clamp_verify_timeout_ms, default_autonomy_policy,
        normalize_read_after_write_targets, rank_projection_list, ranking_candidate_limit,
    };
    use chrono::{Duration, Utc};
    use kura_core::events::BatchEventWarning;
    use kura_core::projections::{Projection, ProjectionFreshness, ProjectionMeta};
    use serde_json::json;
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
        assert_eq!(guard.claim_status, "verified");
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
        assert_eq!(guard.claim_status, "deferred");
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
        assert_eq!(guard.claim_status, "verified");
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
}
