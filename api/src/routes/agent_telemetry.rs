use axum::extract::{Query, State};
use axum::routing::get;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{Postgres, Transaction};
use uuid::Uuid;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

const DEFAULT_WINDOW_HOURS: i32 = 24;
const MAX_WINDOW_HOURS: i32 = 24 * 30;
const DEFAULT_SIGNAL_LIMIT: i64 = 120;
const MAX_SIGNAL_LIMIT: i64 = 500;
const DEFAULT_ANOMALY_LIMIT: i64 = 10;
const MAX_ANOMALY_LIMIT: i64 = 30;

#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct AdminAgentTelemetryOverviewQuery {
    #[serde(default)]
    pub window_hours: Option<i32>,
}

#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct AdminAgentTelemetryAnomaliesQuery {
    #[serde(default)]
    pub window_hours: Option<i32>,
    #[serde(default)]
    pub limit: Option<i64>,
}

#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct AdminAgentTelemetrySignalsQuery {
    #[serde(default)]
    pub window_hours: Option<i32>,
    #[serde(default)]
    pub limit: Option<i64>,
    #[serde(default)]
    pub signal_type: Option<String>,
    #[serde(default)]
    pub user_id: Option<Uuid>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AdminAgentTelemetryOverviewResponse {
    pub generated_at: DateTime<Utc>,
    pub window_hours: i32,
    pub policy_mode: String,
    pub learning_signals: AdminAgentLearningSignalSummary,
    pub requests: AdminAgentRequestSummary,
    pub quality_health: AdminAgentQualityHealthSummary,
    pub plan_updates: AdminAgentPlanUpdateSummary,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AdminAgentLearningSignalSummary {
    pub total: i64,
    pub unique_users: i64,
    pub unique_signal_types: i64,
    pub retrieval_regret_observed: i64,
    pub retrieval_regret_rate_pct: f64,
    pub personal_failure_profile_observed: i64,
    pub laaj_assessed: i64,
    pub laaj_non_pass: i64,
    pub laaj_non_pass_rate_pct: f64,
    pub response_mode_selected: i64,
    pub advisory_scoring_assessed: i64,
    pub advisory_high_hallucination_risk: i64,
    pub advisory_high_hallucination_risk_rate_pct: f64,
    pub advisory_high_data_quality_risk: i64,
    pub advisory_high_data_quality_risk_rate_pct: f64,
    pub advisory_persist_action_persist_now: i64,
    pub advisory_persist_action_draft_preferred: i64,
    pub advisory_persist_action_ask_first: i64,
    pub advisory_cautious_persist_action_rate_pct: f64,
    pub advisory_high_risk_runs: i64,
    pub advisory_high_risk_cautious_actions: i64,
    pub advisory_high_risk_cautious_rate_pct: f64,
    pub advisory_high_risk_persist_now: i64,
    pub advisory_high_risk_persist_now_rate_pct: f64,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AdminAgentRequestSummary {
    pub total_agent_requests: i64,
    pub agent_error_requests: i64,
    pub agent_error_rate_pct: f64,
    pub write_with_proof_requests: i64,
    pub write_with_proof_error_requests: i64,
    pub write_with_proof_error_rate_pct: f64,
    pub write_with_proof_avg_response_time_ms: f64,
    pub write_with_proof_p95_response_time_ms: f64,
    pub context_reads: i64,
    pub context_read_coverage_pct: f64,
    pub visualization_resolve_calls: i64,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AdminAgentQualityHealthSummary {
    pub users_with_projection: i64,
    pub healthy_users: i64,
    pub monitor_users: i64,
    pub degraded_users: i64,
    pub degraded_share_pct: f64,
    pub timezone_context_users: i64,
    pub assumed_timezone_context_users: i64,
    pub assumed_timezone_context_share_pct: f64,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AdminAgentPlanUpdateSummary {
    pub total_training_plan_updates: i64,
    pub high_impact_training_plan_updates: i64,
    pub high_impact_share_pct: f64,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AdminAgentTelemetryAnomaliesResponse {
    pub generated_at: DateTime<Utc>,
    pub window_hours: i32,
    pub anomalies: Vec<AdminAgentTelemetryAnomaly>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct AdminAgentTelemetryAnomaly {
    pub code: String,
    pub severity: String,
    pub title: String,
    pub detail: String,
    pub metric_value: f64,
    pub threshold: f64,
    pub recommendation: String,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AdminAgentTelemetrySignalsResponse {
    pub generated_at: DateTime<Utc>,
    pub window_hours: i32,
    pub signal_type_filter: Option<String>,
    pub user_id_filter: Option<Uuid>,
    pub items: Vec<AdminAgentTelemetrySignalItem>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AdminAgentTelemetrySignalItem {
    pub event_id: Uuid,
    pub timestamp: DateTime<Utc>,
    pub user_id: Uuid,
    pub signal_type: String,
    pub category: String,
    pub workflow_phase: String,
    pub confidence_band: String,
    pub issue_type: String,
    pub invariant_id: String,
    pub cluster_signature: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    pub attributes: Value,
}

#[derive(Debug, sqlx::FromRow)]
struct LearningSignalSummaryRow {
    total: i64,
    unique_users: i64,
    unique_signal_types: i64,
    retrieval_regret_observed: i64,
    personal_failure_profile_observed: i64,
    laaj_assessed: i64,
    laaj_non_pass: i64,
    response_mode_selected: i64,
    advisory_scoring_assessed: i64,
    advisory_high_hallucination_risk: i64,
    advisory_high_data_quality_risk: i64,
    advisory_persist_action_persist_now: i64,
    advisory_persist_action_draft_preferred: i64,
    advisory_persist_action_ask_first: i64,
    advisory_high_risk_runs: i64,
    advisory_high_risk_cautious_actions: i64,
    advisory_high_risk_persist_now: i64,
}

#[derive(Debug, sqlx::FromRow)]
struct AgentRequestSummaryRow {
    total_agent_requests: i64,
    agent_error_requests: i64,
    write_with_proof_requests: i64,
    write_with_proof_error_requests: i64,
    write_with_proof_avg_response_time_ms: Option<f64>,
    write_with_proof_p95_response_time_ms: Option<f64>,
    context_reads: i64,
    visualization_resolve_calls: i64,
}

#[derive(Debug, sqlx::FromRow)]
struct QualityHealthSummaryRow {
    users_with_projection: i64,
    healthy_users: i64,
    monitor_users: i64,
    degraded_users: i64,
}

#[derive(Debug, sqlx::FromRow)]
struct TemporalContextSummaryRow {
    timezone_context_users: i64,
    assumed_timezone_context_users: i64,
}

#[derive(Debug, sqlx::FromRow)]
struct PlanUpdateSummaryRow {
    total_training_plan_updates: i64,
    high_impact_training_plan_updates: i64,
}

#[derive(Debug, sqlx::FromRow)]
struct LearningSignalEventRow {
    event_id: Uuid,
    timestamp: DateTime<Utc>,
    user_id: Uuid,
    data: Value,
    metadata: Value,
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
            docs_hint: Some("Only admin users can view agent telemetry.".to_string()),
        })
    }
}

async fn begin_admin_worker_tx<'a>(
    state: &'a AppState,
    auth: &AuthenticatedUser,
) -> Result<Transaction<'a, Postgres>, AppError> {
    ensure_admin(state, auth).await?;
    let mut tx = state.db.begin().await.map_err(AppError::Database)?;
    sqlx::query("SET LOCAL ROLE app_worker")
        .execute(tx.as_mut())
        .await
        .map_err(AppError::Database)?;
    Ok(tx)
}

fn normalize_window_hours(value: Option<i32>) -> i32 {
    value
        .unwrap_or(DEFAULT_WINDOW_HOURS)
        .clamp(1, MAX_WINDOW_HOURS)
}

fn normalize_limit(value: Option<i64>, default_value: i64, max_value: i64) -> i64 {
    value.unwrap_or(default_value).clamp(1, max_value)
}

fn normalize_optional_text(value: Option<String>) -> Option<String> {
    value.and_then(|raw| {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn round_one_decimal(value: f64) -> f64 {
    if !value.is_finite() {
        return 0.0;
    }
    (value * 10.0).round() / 10.0
}

fn rate_pct(numerator: i64, denominator: i64) -> f64 {
    if denominator <= 0 {
        0.0
    } else {
        round_one_decimal((numerator as f64) / (denominator as f64) * 100.0)
    }
}

fn read_value_at_path<'a>(value: &'a Value, path: &[&str]) -> Option<&'a Value> {
    let mut cursor = value;
    for key in path {
        cursor = cursor.get(*key)?;
    }
    Some(cursor)
}

fn read_string_at_path(value: &Value, path: &[&str]) -> Option<String> {
    read_value_at_path(value, path)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(str::to_string)
}

fn severity_rank(severity: &str) -> i32 {
    match severity {
        "critical" => 0,
        "warning" => 1,
        _ => 2,
    }
}

fn build_anomalies(
    overview: &AdminAgentTelemetryOverviewResponse,
    limit: usize,
) -> Vec<AdminAgentTelemetryAnomaly> {
    let mut anomalies = Vec::new();

    if overview.learning_signals.total < 10 {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "low_signal_volume".to_string(),
            severity: "info".to_string(),
            title: "Learning telemetry is sparse".to_string(),
            detail: "Signal volume is too low for robust trend interpretation.".to_string(),
            metric_value: overview.learning_signals.total as f64,
            threshold: 10.0,
            recommendation:
                "Keep advisory mode and gather more interactions before tightening thresholds."
                    .to_string(),
        });
    }

    if overview.learning_signals.retrieval_regret_observed >= 5
        && overview.learning_signals.retrieval_regret_rate_pct >= 30.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "retrieval_regret_rate_high".to_string(),
            severity: "warning".to_string(),
            title: "Retrieval-regret rate is elevated".to_string(),
            detail:
                "A large share of sessions emitted retrieval-regret, indicating missing or stale evidence in context blocks."
                    .to_string(),
            metric_value: overview.learning_signals.retrieval_regret_rate_pct,
            threshold: 30.0,
            recommendation:
                "Inspect missing evidence patterns and improve context packaging quality."
                    .to_string(),
        });
    }

    if overview.learning_signals.laaj_assessed >= 10
        && overview.learning_signals.laaj_non_pass_rate_pct >= 25.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "laaj_non_pass_rate_high".to_string(),
            severity: "warning".to_string(),
            title: "LaaJ sidecar flags many responses".to_string(),
            detail: "The sidecar frequently does not return pass, suggesting reasoning quality drift or missing context grounding.".to_string(),
            metric_value: overview.learning_signals.laaj_non_pass_rate_pct,
            threshold: 25.0,
            recommendation:
                "Review failing sessions and refine prompts/context before considering stricter controls."
                    .to_string(),
        });
    }

    if overview.learning_signals.advisory_scoring_assessed >= 10
        && overview
            .learning_signals
            .advisory_high_hallucination_risk_rate_pct
            >= 35.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "advisory_hallucination_risk_high".to_string(),
            severity: "warning".to_string(),
            title: "Advisory hallucination risk is frequently high".to_string(),
            detail:
                "A large share of advisory scoring runs reports high hallucination risk, indicating weak grounding before responses."
                    .to_string(),
            metric_value: overview
                .learning_signals
                .advisory_high_hallucination_risk_rate_pct,
            threshold: 35.0,
            recommendation:
                "Inspect high-risk sessions and tighten evidence packaging before personalization."
                    .to_string(),
        });
    }

    if overview.learning_signals.advisory_scoring_assessed >= 10
        && overview
            .learning_signals
            .advisory_high_data_quality_risk_rate_pct
            >= 35.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "advisory_data_quality_risk_high".to_string(),
            severity: "warning".to_string(),
            title: "Advisory data-quality risk is frequently high".to_string(),
            detail:
                "Advisory scoring often flags elevated persistence risk, which can degrade backend data integrity if ignored."
                    .to_string(),
            metric_value: overview
                .learning_signals
                .advisory_high_data_quality_risk_rate_pct,
            threshold: 35.0,
            recommendation:
                "Prefer draft/ask-first persistence in risky paths and verify proof coverage gaps."
                    .to_string(),
        });
    }

    if overview.learning_signals.advisory_high_risk_runs >= 10
        && overview
            .learning_signals
            .advisory_high_risk_persist_now_rate_pct
            >= 25.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "advisory_high_risk_persist_now_rate_high".to_string(),
            severity: "warning".to_string(),
            title: "High-risk sessions still persist too aggressively".to_string(),
            detail:
                "In high-risk advisory runs, persist_now appears too often, which weakens the intended risk-reduction nudges."
                    .to_string(),
            metric_value: overview
                .learning_signals
                .advisory_high_risk_persist_now_rate_pct,
            threshold: 25.0,
            recommendation:
                "Tighten ask-first/draft mapping for high-risk bands and verify uncertainty wording coverage."
                    .to_string(),
        });
    }

    if overview.requests.write_with_proof_requests >= 20
        && overview.requests.write_with_proof_error_rate_pct >= 8.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "write_with_proof_error_rate_high".to_string(),
            severity: "critical".to_string(),
            title: "Write-with-proof error rate is high".to_string(),
            detail:
                "Agent write path is failing often enough to threaten data freshness and trust."
                    .to_string(),
            metric_value: overview.requests.write_with_proof_error_rate_pct,
            threshold: 8.0,
            recommendation:
                "Inspect failing write receipts and stabilize read-after-write verification path."
                    .to_string(),
        });
    }

    if overview.requests.total_agent_requests >= 30 && overview.requests.agent_error_rate_pct >= 6.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "agent_error_rate_high".to_string(),
            severity: "warning".to_string(),
            title: "Overall agent error rate is high".to_string(),
            detail: "Agent endpoints return elevated non-2xx responses within the selected window."
                .to_string(),
            metric_value: overview.requests.agent_error_rate_pct,
            threshold: 6.0,
            recommendation: "Break down failures by endpoint and reduce repeated retry loops."
                .to_string(),
        });
    }

    if overview.quality_health.users_with_projection >= 20
        && overview.quality_health.degraded_share_pct >= 15.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "quality_health_degraded_share_high".to_string(),
            severity: "warning".to_string(),
            title: "Many users are in degraded quality health".to_string(),
            detail: "A significant share of quality_health projections are degraded.".to_string(),
            metric_value: overview.quality_health.degraded_share_pct,
            threshold: 15.0,
            recommendation:
                "Prioritize deterministic repair paths before enabling stricter autonomy."
                    .to_string(),
        });
    }

    if overview.plan_updates.total_training_plan_updates >= 5
        && overview.plan_updates.high_impact_share_pct >= 40.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "high_impact_plan_updates_spike".to_string(),
            severity: "info".to_string(),
            title: "High-impact plan updates are frequent".to_string(),
            detail:
                "Large plan changes dominate updates and may require additional user alignment."
                    .to_string(),
            metric_value: overview.plan_updates.high_impact_share_pct,
            threshold: 40.0,
            recommendation:
                "Verify that high-impact recommendations include clear rationale and alternatives."
                    .to_string(),
        });
    }

    if overview.requests.write_with_proof_requests > 0 && overview.requests.context_reads == 0 {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "writes_without_context_reads".to_string(),
            severity: "info".to_string(),
            title: "Writes happened without context reads".to_string(),
            detail:
                "Agent write attempts were observed without matching /v1/agent/context reads in the same window."
                    .to_string(),
            metric_value: overview.requests.write_with_proof_requests as f64,
            threshold: 1.0,
            recommendation:
                "Ensure the client fetches fresh agent context before write-with-proof calls."
                    .to_string(),
        });
    }

    if overview.requests.write_with_proof_requests >= 5
        && overview.requests.context_read_coverage_pct < 80.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "context_read_coverage_low".to_string(),
            severity: "warning".to_string(),
            title: "Context-read coverage before writes is low".to_string(),
            detail:
                "A notable share of write-with-proof calls is not paired with context reads in the same window."
                    .to_string(),
            metric_value: overview.requests.context_read_coverage_pct,
            threshold: 80.0,
            recommendation:
                "Enforce fresh /v1/agent/context fetches before temporal or high-impact writes."
                    .to_string(),
        });
    }

    if overview.quality_health.timezone_context_users >= 10
        && overview.quality_health.assumed_timezone_context_share_pct >= 25.0
    {
        anomalies.push(AdminAgentTelemetryAnomaly {
            code: "assumed_timezone_share_high".to_string(),
            severity: "info".to_string(),
            title: "Many users still run on assumed timezone context".to_string(),
            detail:
                "A high share of timeline projections uses UTC fallback assumptions instead of explicit user timezone preferences."
                    .to_string(),
            metric_value: overview.quality_health.assumed_timezone_context_share_pct,
            threshold: 25.0,
            recommendation:
                "Prompt timezone preference capture and reduce temporal ambiguity before coaching recommendations."
                    .to_string(),
        });
    }

    anomalies.sort_by(|left, right| {
        severity_rank(&left.severity)
            .cmp(&severity_rank(&right.severity))
            .then_with(|| {
                right
                    .metric_value
                    .partial_cmp(&left.metric_value)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| left.code.cmp(&right.code))
    });

    anomalies.into_iter().take(limit).collect()
}

fn signal_item_from_row(row: LearningSignalEventRow) -> AdminAgentTelemetrySignalItem {
    let signal_type =
        read_string_at_path(&row.data, &["signal_type"]).unwrap_or_else(|| "unknown".to_string());
    let category =
        read_string_at_path(&row.data, &["category"]).unwrap_or_else(|| "unknown".to_string());
    let workflow_phase = read_string_at_path(&row.data, &["signature", "workflow_phase"])
        .unwrap_or_else(|| "unknown".to_string());
    let confidence_band = read_string_at_path(&row.data, &["signature", "confidence_band"])
        .unwrap_or_else(|| "unknown".to_string());
    let issue_type = read_string_at_path(&row.data, &["signature", "issue_type"])
        .unwrap_or_else(|| "unknown".to_string());
    let invariant_id = read_string_at_path(&row.data, &["signature", "invariant_id"])
        .unwrap_or_else(|| "unknown".to_string());
    let cluster_signature = read_string_at_path(&row.data, &["cluster_signature"]);
    let source = read_string_at_path(&row.metadata, &["source"]);
    let agent = read_string_at_path(&row.metadata, &["agent"]);
    let session_id = read_string_at_path(&row.metadata, &["session_id"]);
    let attributes = row
        .data
        .get("attributes")
        .cloned()
        .filter(|value| value.is_object())
        .unwrap_or_else(|| serde_json::json!({}));

    AdminAgentTelemetrySignalItem {
        event_id: row.event_id,
        timestamp: row.timestamp,
        user_id: row.user_id,
        signal_type,
        category,
        workflow_phase,
        confidence_band,
        issue_type,
        invariant_id,
        cluster_signature,
        source,
        agent,
        session_id,
        attributes,
    }
}

async fn load_overview(
    tx: &mut Transaction<'_, Postgres>,
    window_hours: i32,
) -> Result<AdminAgentTelemetryOverviewResponse, AppError> {
    let learning = sqlx::query_as::<_, LearningSignalSummaryRow>(
        r#"
        SELECT
            COUNT(*)::bigint AS total,
            COUNT(DISTINCT e.user_id)::bigint AS unique_users,
            COUNT(DISTINCT (e.data->>'signal_type'))::bigint AS unique_signal_types,
            COUNT(*) FILTER (WHERE e.data->>'signal_type' = 'retrieval_regret_observed')::bigint
                AS retrieval_regret_observed,
            COUNT(*) FILTER (WHERE e.data->>'signal_type' = 'personal_failure_profile_observed')::bigint
                AS personal_failure_profile_observed,
            COUNT(*) FILTER (WHERE e.data->>'signal_type' = 'laaj_sidecar_assessed')::bigint
                AS laaj_assessed,
            COUNT(*) FILTER (
                WHERE e.data->>'signal_type' = 'laaj_sidecar_assessed'
                  AND LOWER(COALESCE(e.data#>>'{attributes,verdict}', 'pass')) <> 'pass'
            )::bigint AS laaj_non_pass,
            COUNT(*) FILTER (WHERE e.data->>'signal_type' = 'response_mode_selected')::bigint
                AS response_mode_selected,
            COUNT(*) FILTER (WHERE e.data->>'signal_type' = 'advisory_scoring_assessed')::bigint
                AS advisory_scoring_assessed,
            COUNT(*) FILTER (
                WHERE e.data->>'signal_type' = 'advisory_scoring_assessed'
                  AND LOWER(COALESCE(e.data#>>'{attributes,hallucination_risk_band}', 'low')) = 'high'
            )::bigint AS advisory_high_hallucination_risk,
            COUNT(*) FILTER (
                WHERE e.data->>'signal_type' = 'advisory_scoring_assessed'
                  AND LOWER(COALESCE(e.data#>>'{attributes,data_quality_risk_band}', 'low')) = 'high'
            )::bigint AS advisory_high_data_quality_risk,
            COUNT(*) FILTER (
                WHERE e.data->>'signal_type' = 'advisory_scoring_assessed'
                  AND LOWER(COALESCE(e.data#>>'{attributes,persist_action}', 'persist_now')) = 'persist_now'
            )::bigint AS advisory_persist_action_persist_now,
            COUNT(*) FILTER (
                WHERE e.data->>'signal_type' = 'advisory_scoring_assessed'
                  AND LOWER(COALESCE(e.data#>>'{attributes,persist_action}', 'persist_now')) = 'draft_preferred'
            )::bigint AS advisory_persist_action_draft_preferred,
            COUNT(*) FILTER (
                WHERE e.data->>'signal_type' = 'advisory_scoring_assessed'
                  AND LOWER(COALESCE(e.data#>>'{attributes,persist_action}', 'persist_now')) = 'ask_first'
            )::bigint AS advisory_persist_action_ask_first,
            COUNT(*) FILTER (
                WHERE e.data->>'signal_type' = 'advisory_scoring_assessed'
                  AND (
                    LOWER(COALESCE(e.data#>>'{attributes,hallucination_risk_band}', 'low')) = 'high'
                    OR LOWER(COALESCE(e.data#>>'{attributes,data_quality_risk_band}', 'low')) = 'high'
                  )
            )::bigint AS advisory_high_risk_runs,
            COUNT(*) FILTER (
                WHERE e.data->>'signal_type' = 'advisory_scoring_assessed'
                  AND (
                    LOWER(COALESCE(e.data#>>'{attributes,hallucination_risk_band}', 'low')) = 'high'
                    OR LOWER(COALESCE(e.data#>>'{attributes,data_quality_risk_band}', 'low')) = 'high'
                  )
                  AND LOWER(COALESCE(e.data#>>'{attributes,persist_action}', 'persist_now')) IN ('ask_first', 'draft_preferred')
            )::bigint AS advisory_high_risk_cautious_actions,
            COUNT(*) FILTER (
                WHERE e.data->>'signal_type' = 'advisory_scoring_assessed'
                  AND (
                    LOWER(COALESCE(e.data#>>'{attributes,hallucination_risk_band}', 'low')) = 'high'
                    OR LOWER(COALESCE(e.data#>>'{attributes,data_quality_risk_band}', 'low')) = 'high'
                  )
                  AND LOWER(COALESCE(e.data#>>'{attributes,persist_action}', 'persist_now')) = 'persist_now'
            )::bigint AS advisory_high_risk_persist_now
        FROM events e
        WHERE e.event_type = 'learning.signal.logged'
          AND e.timestamp >= NOW() - make_interval(hours => $1)
          AND NOT EXISTS (
              SELECT 1
              FROM events r
              WHERE r.event_type = 'event.retracted'
                AND r.data->>'retracted_event_id' = e.id::text
          )
        "#,
    )
    .bind(window_hours)
    .fetch_one(tx.as_mut())
    .await
    .map_err(AppError::Database)?;

    let requests = sqlx::query_as::<_, AgentRequestSummaryRow>(
        r#"
        SELECT
            COUNT(*) FILTER (WHERE path LIKE '/v1/agent/%')::bigint AS total_agent_requests,
            COUNT(*) FILTER (
                WHERE path LIKE '/v1/agent/%'
                  AND status_code >= 400
            )::bigint AS agent_error_requests,
            COUNT(*) FILTER (
                WHERE method = 'POST'
                  AND path = '/v1/agent/write-with-proof'
            )::bigint AS write_with_proof_requests,
            COUNT(*) FILTER (
                WHERE method = 'POST'
                  AND path = '/v1/agent/write-with-proof'
                  AND status_code >= 400
            )::bigint AS write_with_proof_error_requests,
            AVG(response_time_ms) FILTER (
                WHERE method = 'POST'
                  AND path = '/v1/agent/write-with-proof'
            )::float8 AS write_with_proof_avg_response_time_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY response_time_ms) FILTER (
                WHERE method = 'POST'
                  AND path = '/v1/agent/write-with-proof'
            )::float8 AS write_with_proof_p95_response_time_ms,
            COUNT(*) FILTER (
                WHERE method = 'GET'
                  AND path = '/v1/agent/context'
            )::bigint AS context_reads,
            COUNT(*) FILTER (
                WHERE method = 'POST'
                  AND path = '/v1/agent/visualization/resolve'
            )::bigint AS visualization_resolve_calls
        FROM api_access_log
        WHERE timestamp >= NOW() - make_interval(hours => $1)
        "#,
    )
    .bind(window_hours)
    .fetch_one(tx.as_mut())
    .await
    .map_err(AppError::Database)?;

    let quality = sqlx::query_as::<_, QualityHealthSummaryRow>(
        r#"
        SELECT
            COUNT(*)::bigint AS users_with_projection,
            COUNT(*) FILTER (WHERE LOWER(COALESCE(data->>'status', 'unknown')) = 'healthy')::bigint
                AS healthy_users,
            COUNT(*) FILTER (WHERE LOWER(COALESCE(data->>'status', 'unknown')) = 'monitor')::bigint
                AS monitor_users,
            COUNT(*) FILTER (WHERE LOWER(COALESCE(data->>'status', 'unknown')) = 'degraded')::bigint
                AS degraded_users
        FROM projections
        WHERE projection_type = 'quality_health'
          AND key = 'overview'
        "#,
    )
    .fetch_one(tx.as_mut())
    .await
    .map_err(AppError::Database)?;

    let temporal_context = sqlx::query_as::<_, TemporalContextSummaryRow>(
        r#"
        SELECT
            COUNT(*)::bigint AS timezone_context_users,
            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(data#>>'{timezone_context,assumed}', 'false')) = 'true'
            )::bigint AS assumed_timezone_context_users
        FROM projections
        WHERE projection_type = 'training_timeline'
          AND key = 'overview'
        "#,
    )
    .fetch_one(tx.as_mut())
    .await
    .map_err(AppError::Database)?;

    let plans = sqlx::query_as::<_, PlanUpdateSummaryRow>(
        r#"
        SELECT
            COUNT(*)::bigint AS total_training_plan_updates,
            COUNT(*) FILTER (
                WHERE
                    LOWER(COALESCE(e.data->>'change_scope', '')) IN (
                        'full_rewrite',
                        'mesocycle_rewrite',
                        'block_rewrite'
                    )
                    OR (
                        CASE
                            WHEN COALESCE(e.data#>>'{delta,volume_delta_pct}', '') ~ '^-?[0-9]+(\.[0-9]+)?$'
                                THEN (e.data#>>'{delta,volume_delta_pct}')::double precision
                            ELSE 0
                        END
                    ) >= 20
                    OR (
                        CASE
                            WHEN COALESCE(e.data#>>'{delta,intensity_delta_pct}', '') ~ '^-?[0-9]+(\.[0-9]+)?$'
                                THEN (e.data#>>'{delta,intensity_delta_pct}')::double precision
                            ELSE 0
                        END
                    ) >= 10
                    OR (
                        CASE
                            WHEN COALESCE(e.data#>>'{delta,frequency_delta_per_week}', '') ~ '^-?[0-9]+(\.[0-9]+)?$'
                                THEN (e.data#>>'{delta,frequency_delta_per_week}')::double precision
                            ELSE 0
                        END
                    ) >= 2
            )::bigint AS high_impact_training_plan_updates
        FROM events e
        WHERE e.event_type = 'training_plan.updated'
          AND e.timestamp >= NOW() - make_interval(hours => $1)
          AND NOT EXISTS (
              SELECT 1
              FROM events r
              WHERE r.event_type = 'event.retracted'
                AND r.data->>'retracted_event_id' = e.id::text
          )
        "#,
    )
    .bind(window_hours)
    .fetch_one(tx.as_mut())
    .await
    .map_err(AppError::Database)?;

    Ok(AdminAgentTelemetryOverviewResponse {
        generated_at: Utc::now(),
        window_hours,
        policy_mode: "advisory".to_string(),
        learning_signals: AdminAgentLearningSignalSummary {
            total: learning.total,
            unique_users: learning.unique_users,
            unique_signal_types: learning.unique_signal_types,
            retrieval_regret_observed: learning.retrieval_regret_observed,
            retrieval_regret_rate_pct: rate_pct(learning.retrieval_regret_observed, learning.total),
            personal_failure_profile_observed: learning.personal_failure_profile_observed,
            laaj_assessed: learning.laaj_assessed,
            laaj_non_pass: learning.laaj_non_pass,
            laaj_non_pass_rate_pct: rate_pct(learning.laaj_non_pass, learning.laaj_assessed),
            response_mode_selected: learning.response_mode_selected,
            advisory_scoring_assessed: learning.advisory_scoring_assessed,
            advisory_high_hallucination_risk: learning.advisory_high_hallucination_risk,
            advisory_high_hallucination_risk_rate_pct: rate_pct(
                learning.advisory_high_hallucination_risk,
                learning.advisory_scoring_assessed,
            ),
            advisory_high_data_quality_risk: learning.advisory_high_data_quality_risk,
            advisory_high_data_quality_risk_rate_pct: rate_pct(
                learning.advisory_high_data_quality_risk,
                learning.advisory_scoring_assessed,
            ),
            advisory_persist_action_persist_now: learning.advisory_persist_action_persist_now,
            advisory_persist_action_draft_preferred: learning
                .advisory_persist_action_draft_preferred,
            advisory_persist_action_ask_first: learning.advisory_persist_action_ask_first,
            advisory_cautious_persist_action_rate_pct: rate_pct(
                learning.advisory_persist_action_ask_first
                    + learning.advisory_persist_action_draft_preferred,
                learning.advisory_scoring_assessed,
            ),
            advisory_high_risk_runs: learning.advisory_high_risk_runs,
            advisory_high_risk_cautious_actions: learning.advisory_high_risk_cautious_actions,
            advisory_high_risk_cautious_rate_pct: rate_pct(
                learning.advisory_high_risk_cautious_actions,
                learning.advisory_high_risk_runs,
            ),
            advisory_high_risk_persist_now: learning.advisory_high_risk_persist_now,
            advisory_high_risk_persist_now_rate_pct: rate_pct(
                learning.advisory_high_risk_persist_now,
                learning.advisory_high_risk_runs,
            ),
        },
        requests: AdminAgentRequestSummary {
            total_agent_requests: requests.total_agent_requests,
            agent_error_requests: requests.agent_error_requests,
            agent_error_rate_pct: rate_pct(
                requests.agent_error_requests,
                requests.total_agent_requests,
            ),
            write_with_proof_requests: requests.write_with_proof_requests,
            write_with_proof_error_requests: requests.write_with_proof_error_requests,
            write_with_proof_error_rate_pct: rate_pct(
                requests.write_with_proof_error_requests,
                requests.write_with_proof_requests,
            ),
            write_with_proof_avg_response_time_ms: round_one_decimal(
                requests
                    .write_with_proof_avg_response_time_ms
                    .unwrap_or(0.0),
            ),
            write_with_proof_p95_response_time_ms: round_one_decimal(
                requests
                    .write_with_proof_p95_response_time_ms
                    .unwrap_or(0.0),
            ),
            context_reads: requests.context_reads,
            context_read_coverage_pct: rate_pct(
                requests.context_reads,
                requests.write_with_proof_requests,
            ),
            visualization_resolve_calls: requests.visualization_resolve_calls,
        },
        quality_health: AdminAgentQualityHealthSummary {
            users_with_projection: quality.users_with_projection,
            healthy_users: quality.healthy_users,
            monitor_users: quality.monitor_users,
            degraded_users: quality.degraded_users,
            degraded_share_pct: rate_pct(quality.degraded_users, quality.users_with_projection),
            timezone_context_users: temporal_context.timezone_context_users,
            assumed_timezone_context_users: temporal_context.assumed_timezone_context_users,
            assumed_timezone_context_share_pct: rate_pct(
                temporal_context.assumed_timezone_context_users,
                temporal_context.timezone_context_users,
            ),
        },
        plan_updates: AdminAgentPlanUpdateSummary {
            total_training_plan_updates: plans.total_training_plan_updates,
            high_impact_training_plan_updates: plans.high_impact_training_plan_updates,
            high_impact_share_pct: rate_pct(
                plans.high_impact_training_plan_updates,
                plans.total_training_plan_updates,
            ),
        },
    })
}

#[utoipa::path(
    get,
    path = "/v1/admin/agent/telemetry/overview",
    params(AdminAgentTelemetryOverviewQuery),
    responses(
        (status = 200, description = "Agent telemetry overview", body = AdminAgentTelemetryOverviewResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_agent_telemetry_overview(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Query(query): Query<AdminAgentTelemetryOverviewQuery>,
) -> Result<Json<AdminAgentTelemetryOverviewResponse>, AppError> {
    let window_hours = normalize_window_hours(query.window_hours);
    let mut tx = begin_admin_worker_tx(&state, &auth).await?;
    let overview = load_overview(&mut tx, window_hours).await?;
    tx.commit().await.map_err(AppError::Database)?;
    Ok(Json(overview))
}

#[utoipa::path(
    get,
    path = "/v1/admin/agent/telemetry/anomalies",
    params(AdminAgentTelemetryAnomaliesQuery),
    responses(
        (status = 200, description = "Detected agent telemetry anomalies", body = AdminAgentTelemetryAnomaliesResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_agent_telemetry_anomalies(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Query(query): Query<AdminAgentTelemetryAnomaliesQuery>,
) -> Result<Json<AdminAgentTelemetryAnomaliesResponse>, AppError> {
    let window_hours = normalize_window_hours(query.window_hours);
    let anomaly_limit = normalize_limit(query.limit, DEFAULT_ANOMALY_LIMIT, MAX_ANOMALY_LIMIT);

    let mut tx = begin_admin_worker_tx(&state, &auth).await?;
    let overview = load_overview(&mut tx, window_hours).await?;
    tx.commit().await.map_err(AppError::Database)?;

    let anomalies = build_anomalies(&overview, anomaly_limit as usize);

    Ok(Json(AdminAgentTelemetryAnomaliesResponse {
        generated_at: Utc::now(),
        window_hours,
        anomalies,
    }))
}

#[utoipa::path(
    get,
    path = "/v1/admin/agent/telemetry/signals",
    params(AdminAgentTelemetrySignalsQuery),
    responses(
        (status = 200, description = "Recent learning signal feed", body = AdminAgentTelemetrySignalsResponse),
        (status = 401, description = "Not authenticated"),
        (status = 403, description = "Not an admin")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn list_agent_telemetry_signals(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Query(query): Query<AdminAgentTelemetrySignalsQuery>,
) -> Result<Json<AdminAgentTelemetrySignalsResponse>, AppError> {
    let window_hours = normalize_window_hours(query.window_hours);
    let limit = normalize_limit(query.limit, DEFAULT_SIGNAL_LIMIT, MAX_SIGNAL_LIMIT);
    let signal_type_filter = normalize_optional_text(query.signal_type);

    let mut tx = begin_admin_worker_tx(&state, &auth).await?;
    let rows = sqlx::query_as::<_, LearningSignalEventRow>(
        r#"
        SELECT
            e.id AS event_id,
            e.timestamp,
            e.user_id,
            e.data,
            e.metadata
        FROM events e
        WHERE e.event_type = 'learning.signal.logged'
          AND e.timestamp >= NOW() - make_interval(hours => $1)
          AND ($2::text IS NULL OR e.data->>'signal_type' = $2)
          AND ($3::uuid IS NULL OR e.user_id = $3)
          AND NOT EXISTS (
              SELECT 1
              FROM events r
              WHERE r.event_type = 'event.retracted'
                AND r.data->>'retracted_event_id' = e.id::text
          )
        ORDER BY e.timestamp DESC, e.id DESC
        LIMIT $4
        "#,
    )
    .bind(window_hours)
    .bind(signal_type_filter.as_deref())
    .bind(query.user_id)
    .bind(limit)
    .fetch_all(tx.as_mut())
    .await
    .map_err(AppError::Database)?;
    tx.commit().await.map_err(AppError::Database)?;

    let items = rows.into_iter().map(signal_item_from_row).collect();

    Ok(Json(AdminAgentTelemetrySignalsResponse {
        generated_at: Utc::now(),
        window_hours,
        signal_type_filter,
        user_id_filter: query.user_id,
        items,
    }))
}

pub fn admin_router() -> Router<AppState> {
    Router::new()
        .route(
            "/v1/admin/agent/telemetry/overview",
            get(get_agent_telemetry_overview),
        )
        .route(
            "/v1/admin/agent/telemetry/anomalies",
            get(get_agent_telemetry_anomalies),
        )
        .route(
            "/v1/admin/agent/telemetry/signals",
            get(list_agent_telemetry_signals),
        )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn make_overview(
        retrieval_regret_rate_pct: f64,
        laaj_non_pass_rate_pct: f64,
        write_error_rate_pct: f64,
    ) -> AdminAgentTelemetryOverviewResponse {
        AdminAgentTelemetryOverviewResponse {
            generated_at: Utc::now(),
            window_hours: 24,
            policy_mode: "advisory".to_string(),
            learning_signals: AdminAgentLearningSignalSummary {
                total: 100,
                unique_users: 20,
                unique_signal_types: 8,
                retrieval_regret_observed: 35,
                retrieval_regret_rate_pct,
                personal_failure_profile_observed: 10,
                laaj_assessed: 20,
                laaj_non_pass: 6,
                laaj_non_pass_rate_pct,
                response_mode_selected: 30,
                advisory_scoring_assessed: 24,
                advisory_high_hallucination_risk: 6,
                advisory_high_hallucination_risk_rate_pct: 25.0,
                advisory_high_data_quality_risk: 5,
                advisory_high_data_quality_risk_rate_pct: 20.8,
                advisory_persist_action_persist_now: 9,
                advisory_persist_action_draft_preferred: 8,
                advisory_persist_action_ask_first: 7,
                advisory_cautious_persist_action_rate_pct: 62.5,
                advisory_high_risk_runs: 12,
                advisory_high_risk_cautious_actions: 9,
                advisory_high_risk_cautious_rate_pct: 75.0,
                advisory_high_risk_persist_now: 3,
                advisory_high_risk_persist_now_rate_pct: 25.0,
            },
            requests: AdminAgentRequestSummary {
                total_agent_requests: 100,
                agent_error_requests: 7,
                agent_error_rate_pct: 7.0,
                write_with_proof_requests: 40,
                write_with_proof_error_requests: 6,
                write_with_proof_error_rate_pct: write_error_rate_pct,
                write_with_proof_avg_response_time_ms: 130.0,
                write_with_proof_p95_response_time_ms: 260.0,
                context_reads: 40,
                context_read_coverage_pct: 100.0,
                visualization_resolve_calls: 10,
            },
            quality_health: AdminAgentQualityHealthSummary {
                users_with_projection: 50,
                healthy_users: 20,
                monitor_users: 20,
                degraded_users: 10,
                degraded_share_pct: 20.0,
                timezone_context_users: 40,
                assumed_timezone_context_users: 8,
                assumed_timezone_context_share_pct: 20.0,
            },
            plan_updates: AdminAgentPlanUpdateSummary {
                total_training_plan_updates: 8,
                high_impact_training_plan_updates: 4,
                high_impact_share_pct: 50.0,
            },
        }
    }

    #[test]
    fn normalize_window_hours_clamps_values() {
        assert_eq!(normalize_window_hours(None), 24);
        assert_eq!(normalize_window_hours(Some(0)), 1);
        assert_eq!(normalize_window_hours(Some(9999)), MAX_WINDOW_HOURS);
    }

    #[test]
    fn rate_pct_handles_zero_denominator() {
        assert_eq!(rate_pct(3, 0), 0.0);
        assert_eq!(rate_pct(5, 20), 25.0);
    }

    #[test]
    fn build_anomalies_surfaces_critical_before_warning() {
        let overview = make_overview(35.0, 30.0, 12.0);
        let anomalies = build_anomalies(&overview, 10);
        assert!(!anomalies.is_empty());
        assert_eq!(anomalies[0].severity, "critical");
        assert!(
            anomalies
                .iter()
                .any(|item| item.code == "retrieval_regret_rate_high")
        );
    }

    #[test]
    fn build_anomalies_flags_low_context_read_coverage() {
        let mut overview = make_overview(10.0, 10.0, 2.0);
        overview.requests.write_with_proof_requests = 12;
        overview.requests.context_reads = 6;
        overview.requests.context_read_coverage_pct = 50.0;

        let anomalies = build_anomalies(&overview, 10);
        assert!(
            anomalies
                .iter()
                .any(|item| item.code == "context_read_coverage_low")
        );
    }

    #[test]
    fn build_anomalies_flags_advisory_risk_spikes() {
        let mut overview = make_overview(10.0, 10.0, 2.0);
        overview.learning_signals.advisory_scoring_assessed = 25;
        overview.learning_signals.advisory_high_hallucination_risk = 11;
        overview.learning_signals.advisory_high_hallucination_risk_rate_pct = 44.0;
        overview.learning_signals.advisory_high_data_quality_risk = 10;
        overview.learning_signals.advisory_high_data_quality_risk_rate_pct = 40.0;

        let anomalies = build_anomalies(&overview, 10);
        assert!(
            anomalies
                .iter()
                .any(|item| item.code == "advisory_hallucination_risk_high")
        );
        assert!(
            anomalies
                .iter()
                .any(|item| item.code == "advisory_data_quality_risk_high")
        );
    }

    #[test]
    fn build_anomalies_flags_advisory_nudge_effectiveness_gap() {
        let mut overview = make_overview(10.0, 10.0, 2.0);
        overview.learning_signals.advisory_high_risk_runs = 22;
        overview.learning_signals.advisory_high_risk_persist_now = 9;
        overview.learning_signals.advisory_high_risk_persist_now_rate_pct = 40.9;
        overview.learning_signals.advisory_high_risk_cautious_actions = 13;
        overview.learning_signals.advisory_high_risk_cautious_rate_pct = 59.1;

        let anomalies = build_anomalies(&overview, 10);
        assert!(
            anomalies
                .iter()
                .any(|item| item.code == "advisory_high_risk_persist_now_rate_high")
        );
    }

    #[test]
    fn signal_item_from_row_extracts_nested_fields() {
        let row = LearningSignalEventRow {
            event_id: Uuid::now_v7(),
            timestamp: Utc::now(),
            user_id: Uuid::now_v7(),
            data: json!({
                "signal_type": "retrieval_regret_observed",
                "category": "friction_signal",
                "cluster_signature": "ls_abc123",
                "signature": {
                    "workflow_phase": "agent_write_with_proof",
                    "confidence_band": "medium",
                    "issue_type": "retrieval_regret",
                    "invariant_id": "rg_01"
                },
                "attributes": {
                    "regret_score": 0.75
                }
            }),
            metadata: json!({
                "source": "agent_write_with_proof",
                "agent": "api",
                "session_id": "learning:retrieval-regret"
            }),
        };

        let item = signal_item_from_row(row);

        assert_eq!(item.signal_type, "retrieval_regret_observed");
        assert_eq!(item.workflow_phase, "agent_write_with_proof");
        assert_eq!(item.confidence_band, "medium");
        assert_eq!(item.source.as_deref(), Some("agent_write_with_proof"));
        assert_eq!(item.attributes["regret_score"], json!(0.75));
    }
}
