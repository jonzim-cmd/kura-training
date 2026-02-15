use std::collections::HashMap;
use std::convert::Infallible;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};
use std::time::Instant;

use axum::extract::Request;
use axum::http::{HeaderValue, Response, StatusCode};
use axum::response::IntoResponse;
use chrono::{DateTime, Duration as ChronoDuration, Utc};
use serde_json::json;
use tokio::sync::RwLock;
use tower::{Layer, Service, ServiceExt};
use uuid::Uuid;

use crate::auth::AuthenticatedUser;
use crate::security_profile::{SecurityProfile, resolve_security_profile};

const BURST_REQUEST_THRESHOLD_60S: i32 = 25;
const DENIED_RATIO_THRESHOLD_60S: f64 = 0.45;
const DENIED_RATIO_BLOCK_THRESHOLD_60S: f64 = 0.55;
const MIN_REQUESTS_FOR_DENIED_RATIO: i32 = 10;
const MIN_REQUESTS_FOR_DENIED_RATIO_BLOCK: i32 = 30;
const UNIQUE_PATH_THRESHOLD_60S: i32 = 5;
const CONTEXT_READ_THRESHOLD_60S: i32 = 8;
const WRITE_BURST_THRESHOLD_60S: i32 = 12;
const ALLOW_TELEMETRY_SAMPLE_BUCKET_THRESHOLD: i16 = 20;
const DENIED_RATIO_PRIOR_DENIED: f64 = 2.0;
const DENIED_RATIO_PRIOR_TOTAL: f64 = 8.0;
const NOT_FOUND_DENIED_WEIGHT: f64 = 0.35;

#[derive(Clone)]
pub struct AdaptiveAbuseLayer {
    pool: sqlx::PgPool,
    state: Arc<RwLock<HashMap<Uuid, CooldownState>>>,
}

pub fn agent_layer(pool: sqlx::PgPool) -> AdaptiveAbuseLayer {
    AdaptiveAbuseLayer {
        pool,
        state: Arc::new(RwLock::new(HashMap::new())),
    }
}

impl<S> Layer<S> for AdaptiveAbuseLayer {
    type Service = AdaptiveAbuseService<S>;

    fn layer(&self, inner: S) -> Self::Service {
        AdaptiveAbuseService {
            inner,
            pool: self.pool.clone(),
            state: self.state.clone(),
        }
    }
}

#[derive(Clone)]
pub struct AdaptiveAbuseService<S> {
    inner: S,
    pool: sqlx::PgPool,
    state: Arc<RwLock<HashMap<Uuid, CooldownState>>>,
}

impl<S> Service<Request> for AdaptiveAbuseService<S>
where
    S: Service<Request, Response = axum::response::Response, Error = Infallible>
        + Clone
        + Send
        + 'static,
    S::Future: Send + 'static,
{
    type Response = axum::response::Response;
    type Error = Infallible;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, Self::Error>> + Send>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, req: Request) -> Self::Future {
        let not_ready = self.inner.clone();
        let ready = std::mem::replace(&mut self.inner, not_ready);
        let pool = self.pool.clone();
        let state = self.state.clone();

        Box::pin(async move {
            let path = req.uri().path().to_string();
            if !path.starts_with("/v1/agent/") {
                return Ok(ready.oneshot(req).await.into_response());
            }

            let method = req.method().to_string();
            let user_id = req
                .extensions()
                .get::<AuthenticatedUser>()
                .map(|auth| auth.user_id);

            let Some(user_id) = user_id else {
                return Ok(ready.oneshot(req).await.into_response());
            };

            let now = Utc::now();
            let resolved_profile = match resolve_security_profile(&pool, user_id).await {
                Ok(profile) => profile,
                Err(err) => {
                    tracing::warn!(
                        error = %err,
                        user_id = %user_id,
                        "security profile resolution failed; falling back to adaptive"
                    );
                    crate::security_profile::ResolvedSecurityProfile {
                        profile: SecurityProfile::Adaptive,
                        source: "fallback".to_string(),
                        rollout_bucket: 0,
                    }
                }
            };
            let snapshot = match fetch_recent_agent_access_snapshot(&pool, user_id).await {
                Ok(snapshot) => snapshot,
                Err(err) => {
                    tracing::warn!(
                        error = %err,
                        user_id = %user_id,
                        "adaptive abuse check skipped due to access-log query failure"
                    );
                    return Ok(ready.oneshot(req).await.into_response());
                }
            };
            let assessment = evaluate_abuse_risk(&snapshot, resolved_profile.profile);
            let decision = apply_cooldown_policy(&state, user_id, &assessment, now).await;

            if decision.action == AbuseAction::Block {
                let mut response = build_block_response(&assessment, &decision);
                if let Ok(value) = HeaderValue::from_str(assessment.profile.as_str()) {
                    response
                        .headers_mut()
                        .insert("x-kura-security-profile", value);
                }
                if let Ok(value) = HeaderValue::from_str(&resolved_profile.source) {
                    response
                        .headers_mut()
                        .insert("x-kura-security-profile-source", value);
                }
                if let Ok(value) =
                    HeaderValue::from_str(&resolved_profile.rollout_bucket.to_string())
                {
                    response
                        .headers_mut()
                        .insert("x-kura-security-profile-bucket", value);
                }
                persist_adaptive_abuse_telemetry(
                    &pool,
                    AdaptiveAbuseTelemetryRecord {
                        user_id,
                        profile: assessment.profile,
                        path: path.clone(),
                        method: method.clone(),
                        action: decision.action,
                        risk_score: assessment.score,
                        cooldown_active: decision.cooldown_active,
                        cooldown_until: decision.cooldown_until,
                        total_requests_60s: assessment.snapshot.total_requests_60s,
                        denied_requests_60s: assessment.snapshot.denied_requests_60s,
                        unique_paths_60s: assessment.snapshot.unique_paths_60s,
                        context_reads_60s: assessment.snapshot.context_reads_60s,
                        denied_ratio_60s: assessment.snapshot.denied_ratio_60s(),
                        signals: assessment.signals.clone(),
                        false_positive_hint: false,
                        ux_impact_hint: "blocked".to_string(),
                        response_status_code: StatusCode::TOO_MANY_REQUESTS.as_u16() as i16,
                        response_time_ms: 0,
                    },
                )
                .await;

                return Ok(response);
            }

            let started = Instant::now();
            if decision.action == AbuseAction::Throttle {
                tokio::time::sleep(std::time::Duration::from_millis(decision.throttle_delay_ms))
                    .await;
            }

            let mut response = ready.oneshot(req).await.into_response();
            annotate_response_headers(&mut response, &assessment, &decision);
            let status_code = response.status().as_u16() as i16;
            let response_time_ms = started.elapsed().as_millis().min(i32::MAX as u128) as i32;

            let allow_sampled = decision.action == AbuseAction::Allow
                && resolved_profile.rollout_bucket < ALLOW_TELEMETRY_SAMPLE_BUCKET_THRESHOLD;
            if should_persist_adaptive_telemetry(&assessment, &decision, allow_sampled) {
                let false_positive_hint = decision.action == AbuseAction::Throttle
                    && (200..300).contains(&(status_code as i32))
                    && assessment.snapshot.denied_ratio_60s() < 0.1
                    && assessment.signals.len() == 1;
                let ux_impact_hint = if decision.action == AbuseAction::Throttle {
                    "delayed".to_string()
                } else {
                    "none".to_string()
                };
                persist_adaptive_abuse_telemetry(
                    &pool,
                    AdaptiveAbuseTelemetryRecord {
                        user_id,
                        profile: assessment.profile,
                        path: path.clone(),
                        method: method.clone(),
                        action: decision.action,
                        risk_score: assessment.score,
                        cooldown_active: decision.cooldown_active,
                        cooldown_until: decision.cooldown_until,
                        total_requests_60s: assessment.snapshot.total_requests_60s,
                        denied_requests_60s: assessment.snapshot.denied_requests_60s,
                        unique_paths_60s: assessment.snapshot.unique_paths_60s,
                        context_reads_60s: assessment.snapshot.context_reads_60s,
                        denied_ratio_60s: assessment.snapshot.denied_ratio_60s(),
                        signals: assessment.signals.clone(),
                        false_positive_hint,
                        ux_impact_hint,
                        response_status_code: status_code,
                        response_time_ms,
                    },
                )
                .await;
            }

            if decision.recovered_from_cooldown {
                persist_adaptive_abuse_telemetry(
                    &pool,
                    AdaptiveAbuseTelemetryRecord {
                        user_id,
                        profile: assessment.profile,
                        path,
                        method,
                        action: AbuseAction::Recovery,
                        risk_score: assessment.score,
                        cooldown_active: false,
                        cooldown_until: None,
                        total_requests_60s: assessment.snapshot.total_requests_60s,
                        denied_requests_60s: assessment.snapshot.denied_requests_60s,
                        unique_paths_60s: assessment.snapshot.unique_paths_60s,
                        context_reads_60s: assessment.snapshot.context_reads_60s,
                        denied_ratio_60s: assessment.snapshot.denied_ratio_60s(),
                        signals: vec!["cooldown_recovered".to_string()],
                        false_positive_hint: false,
                        ux_impact_hint: "none".to_string(),
                        response_status_code: status_code,
                        response_time_ms,
                    },
                )
                .await;
            }

            if let Ok(value) = HeaderValue::from_str(assessment.profile.as_str()) {
                response
                    .headers_mut()
                    .insert("x-kura-security-profile", value);
            }
            if let Ok(value) = HeaderValue::from_str(&resolved_profile.source) {
                response
                    .headers_mut()
                    .insert("x-kura-security-profile-source", value);
            }
            if let Ok(value) = HeaderValue::from_str(&resolved_profile.rollout_bucket.to_string()) {
                response
                    .headers_mut()
                    .insert("x-kura-security-profile-bucket", value);
            }

            Ok(response)
        })
    }
}

#[derive(Debug, Clone)]
struct CooldownState {
    cooldown_until: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum AbuseAction {
    Allow,
    Throttle,
    Block,
    Recovery,
}

#[derive(Debug, Clone, Copy)]
struct ProfileTuning {
    throttle_score_threshold: i32,
    block_score_threshold: i32,
    throttle_cooldown_secs: i64,
    block_cooldown_secs: i64,
    throttle_delay_low_ms: u64,
    throttle_delay_medium_ms: u64,
    throttle_delay_high_ms: u64,
}

fn profile_tuning(profile: SecurityProfile) -> ProfileTuning {
    match profile {
        // Default profile should remain low-friction for normal users.
        SecurityProfile::Default => ProfileTuning {
            throttle_score_threshold: 70,
            block_score_threshold: 90,
            throttle_cooldown_secs: 15,
            block_cooldown_secs: 45,
            throttle_delay_low_ms: 90,
            throttle_delay_medium_ms: 140,
            throttle_delay_high_ms: 200,
        },
        SecurityProfile::Adaptive => ProfileTuning {
            throttle_score_threshold: 40,
            block_score_threshold: 75,
            throttle_cooldown_secs: 45,
            block_cooldown_secs: 120,
            throttle_delay_low_ms: 150,
            throttle_delay_medium_ms: 300,
            throttle_delay_high_ms: 500,
        },
        SecurityProfile::Strict => ProfileTuning {
            throttle_score_threshold: 25,
            block_score_threshold: 55,
            throttle_cooldown_secs: 90,
            block_cooldown_secs: 180,
            throttle_delay_low_ms: 350,
            throttle_delay_medium_ms: 550,
            throttle_delay_high_ms: 800,
        },
    }
}

#[derive(Debug, Clone)]
struct AbuseDecision {
    action: AbuseAction,
    cooldown_active: bool,
    cooldown_until: Option<DateTime<Utc>>,
    throttle_delay_ms: u64,
    retry_after_secs: u64,
    recovered_from_cooldown: bool,
}

#[derive(Debug, Clone, Default)]
struct AccessSignalSnapshot {
    total_requests_60s: i32,
    denied_requests_60s: i32,
    denied_authz_requests_60s: i32,
    denied_not_found_requests_60s: i32,
    unique_paths_60s: i32,
    context_reads_60s: i32,
    write_requests_60s: i32,
}

impl AccessSignalSnapshot {
    fn denied_ratio_60s(&self) -> f64 {
        if self.total_requests_60s <= 0 {
            0.0
        } else {
            self.denied_requests_60s as f64 / self.total_requests_60s as f64
        }
    }

    fn smoothed_weighted_denied_ratio_60s(&self) -> f64 {
        let total = self.total_requests_60s.max(0) as f64;
        let weighted_denied = self.denied_authz_requests_60s.max(0) as f64
            + self.denied_not_found_requests_60s.max(0) as f64 * NOT_FOUND_DENIED_WEIGHT;
        let denominator = total + DENIED_RATIO_PRIOR_TOTAL;
        if denominator <= 0.0 {
            return 0.0;
        }
        (weighted_denied + DENIED_RATIO_PRIOR_DENIED) / denominator
    }
}

#[derive(Debug, Clone)]
struct AbuseRiskAssessment {
    snapshot: AccessSignalSnapshot,
    profile: SecurityProfile,
    score: i32,
    signals: Vec<String>,
    base_action: AbuseAction,
}

#[derive(sqlx::FromRow)]
struct AccessSignalAggregateRow {
    total_requests_60s: i32,
    denied_requests_60s: i32,
    denied_authz_requests_60s: i32,
    denied_not_found_requests_60s: i32,
    unique_paths_60s: i32,
    context_reads_60s: i32,
    write_requests_60s: i32,
}

#[derive(Debug, Clone)]
struct AdaptiveAbuseTelemetryRecord {
    user_id: Uuid,
    profile: SecurityProfile,
    path: String,
    method: String,
    action: AbuseAction,
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

async fn fetch_recent_agent_access_snapshot(
    pool: &sqlx::PgPool,
    user_id: Uuid,
) -> Result<AccessSignalSnapshot, sqlx::Error> {
    let row = sqlx::query_as::<_, AccessSignalAggregateRow>(
        r#"
        SELECT
            COUNT(*)::int AS total_requests_60s,
            COUNT(*) FILTER (WHERE status_code IN (401, 403, 404))::int AS denied_requests_60s,
            COUNT(*) FILTER (WHERE status_code IN (401, 403))::int AS denied_authz_requests_60s,
            COUNT(*) FILTER (WHERE status_code = 404)::int AS denied_not_found_requests_60s,
            COUNT(DISTINCT CASE
                WHEN path LIKE '/v1/agent/evidence/event/%' THEN '/v1/agent/evidence/event/{event_id}'
                ELSE path
            END)::int AS unique_paths_60s,
            COUNT(*) FILTER (WHERE path = '/v1/agent/context')::int AS context_reads_60s,
            COUNT(*) FILTER (WHERE path = '/v1/agent/write-with-proof')::int AS write_requests_60s
        FROM api_access_log
        WHERE user_id = $1
          AND path LIKE '/v1/agent/%'
          AND timestamp >= NOW() - INTERVAL '60 seconds'
        "#,
    )
    .bind(user_id)
    .fetch_one(pool)
    .await?;

    Ok(AccessSignalSnapshot {
        total_requests_60s: row.total_requests_60s,
        denied_requests_60s: row.denied_requests_60s,
        denied_authz_requests_60s: row.denied_authz_requests_60s,
        denied_not_found_requests_60s: row.denied_not_found_requests_60s,
        unique_paths_60s: row.unique_paths_60s,
        context_reads_60s: row.context_reads_60s,
        write_requests_60s: row.write_requests_60s,
    })
}

fn normalize_agent_path_for_signals(path: &str) -> String {
    if path.starts_with("/v1/agent/evidence/event/") {
        "/v1/agent/evidence/event/{event_id}".to_string()
    } else {
        path.to_string()
    }
}

fn evaluate_abuse_risk(
    snapshot: &AccessSignalSnapshot,
    profile: SecurityProfile,
) -> AbuseRiskAssessment {
    let tuning = profile_tuning(profile);
    let mut score = 0;
    let mut signals = Vec::new();
    let smoothed_denied_ratio = snapshot.smoothed_weighted_denied_ratio_60s();

    if snapshot.total_requests_60s >= BURST_REQUEST_THRESHOLD_60S {
        score += 30;
        signals.push("burst_rate_60s".to_string());
    }

    if snapshot.total_requests_60s >= MIN_REQUESTS_FOR_DENIED_RATIO
        && smoothed_denied_ratio >= DENIED_RATIO_THRESHOLD_60S
    {
        score += 20;
        signals.push("denied_ratio_spike_60s".to_string());
    }
    if snapshot.total_requests_60s >= MIN_REQUESTS_FOR_DENIED_RATIO_BLOCK
        && smoothed_denied_ratio >= DENIED_RATIO_BLOCK_THRESHOLD_60S
    {
        score += 25;
        signals.push("denied_ratio_high_confidence_60s".to_string());
    }
    if snapshot.total_requests_60s >= 12 && snapshot.denied_authz_requests_60s >= 8 {
        score += 20;
        signals.push("authz_denied_burst_60s".to_string());
    }

    if snapshot.unique_paths_60s >= UNIQUE_PATH_THRESHOLD_60S
        && snapshot.denied_requests_60s >= 3
        && snapshot.total_requests_60s >= 12
    {
        score += 20;
        signals.push("endpoint_enumeration_pattern_60s".to_string());
    }

    if snapshot.context_reads_60s >= CONTEXT_READ_THRESHOLD_60S {
        score += 20;
        signals.push("context_scrape_burst_60s".to_string());
    }

    if snapshot.write_requests_60s >= WRITE_BURST_THRESHOLD_60S {
        score += 25;
        signals.push("write_burst_60s".to_string());
    }

    let base_action = if score >= tuning.block_score_threshold {
        AbuseAction::Block
    } else if score >= tuning.throttle_score_threshold {
        AbuseAction::Throttle
    } else {
        AbuseAction::Allow
    };

    AbuseRiskAssessment {
        snapshot: snapshot.clone(),
        profile,
        score,
        signals,
        base_action,
    }
}

async fn apply_cooldown_policy(
    state: &Arc<RwLock<HashMap<Uuid, CooldownState>>>,
    user_id: Uuid,
    assessment: &AbuseRiskAssessment,
    now: DateTime<Utc>,
) -> AbuseDecision {
    let tuning = profile_tuning(assessment.profile);
    let mut recovered_from_cooldown = false;
    let mut lock = state.write().await;

    let active_cooldown_until = match lock.get(&user_id) {
        Some(entry) if entry.cooldown_until > now => Some(entry.cooldown_until),
        Some(_) => {
            lock.remove(&user_id);
            recovered_from_cooldown = true;
            None
        }
        None => None,
    };

    let mut action = assessment.base_action;
    let mut cooldown_until = active_cooldown_until;

    match assessment.base_action {
        AbuseAction::Block => {
            let target = now + ChronoDuration::seconds(tuning.block_cooldown_secs);
            cooldown_until = Some(cooldown_until.map_or(target, |existing| existing.max(target)));
            action = AbuseAction::Block;
        }
        AbuseAction::Throttle => {
            let target = now + ChronoDuration::seconds(tuning.throttle_cooldown_secs);
            cooldown_until = Some(cooldown_until.map_or(target, |existing| existing.max(target)));
            action = AbuseAction::Throttle;
        }
        AbuseAction::Allow => {
            if cooldown_until.is_some() {
                action = AbuseAction::Throttle;
            }
        }
        AbuseAction::Recovery => {}
    }

    if let Some(until) = cooldown_until {
        lock.insert(
            user_id,
            CooldownState {
                cooldown_until: until,
            },
        );
    } else {
        lock.remove(&user_id);
    }

    let cooldown_active = cooldown_until.is_some_and(|until| until > now);
    let retry_after_secs = cooldown_until
        .map(|until| (until - now).num_seconds().max(1) as u64)
        .unwrap_or(0);
    let throttle_delay_ms = match action {
        AbuseAction::Throttle if assessment.score >= tuning.block_score_threshold => {
            tuning.throttle_delay_high_ms
        }
        AbuseAction::Throttle if assessment.score >= tuning.throttle_score_threshold + 10 => {
            tuning.throttle_delay_medium_ms
        }
        AbuseAction::Throttle => tuning.throttle_delay_low_ms,
        _ => 0,
    };

    AbuseDecision {
        action,
        cooldown_active,
        cooldown_until,
        throttle_delay_ms,
        retry_after_secs,
        recovered_from_cooldown: recovered_from_cooldown
            && action == AbuseAction::Allow
            && !cooldown_active,
    }
}

fn build_block_response(
    assessment: &AbuseRiskAssessment,
    decision: &AbuseDecision,
) -> axum::response::Response {
    let retry_after_secs = decision.retry_after_secs.max(1);
    let request_id = Uuid::now_v7().to_string();
    let body = json!({
        "error": kura_core::error::codes::RATE_LIMITED,
        "message": format!("Adaptive abuse protection active. Retry after {retry_after_secs} seconds."),
        "field": "security_abuse",
        "received": {
            "risk_score": assessment.score,
            "signals": assessment.signals,
            "window": {
                "total_requests_60s": assessment.snapshot.total_requests_60s,
                "denied_requests_60s": assessment.snapshot.denied_requests_60s,
                "unique_paths_60s": assessment.snapshot.unique_paths_60s,
                "context_reads_60s": assessment.snapshot.context_reads_60s
            }
        },
        "request_id": request_id,
        "docs_hint": "Reduce high-frequency or enumeration-like agent calls and retry after cooldown."
    });

    let mut response = Response::builder()
        .status(StatusCode::TOO_MANY_REQUESTS)
        .header("content-type", "application/json")
        .header("retry-after", retry_after_secs.to_string())
        .body(axum::body::Body::from(body.to_string()))
        .expect("adaptive abuse block response should build");
    annotate_response_headers(&mut response, assessment, decision);
    response
}

fn annotate_response_headers(
    response: &mut axum::response::Response,
    assessment: &AbuseRiskAssessment,
    decision: &AbuseDecision,
) {
    let action = action_label(decision.action);
    if let Ok(value) = HeaderValue::from_str(action) {
        response.headers_mut().insert("x-kura-abuse-action", value);
    }
    if let Ok(value) = HeaderValue::from_str(&assessment.score.to_string()) {
        response.headers_mut().insert("x-kura-abuse-score", value);
    }
    if !assessment.signals.is_empty() {
        let joined = assessment.signals.join(",");
        if let Ok(value) = HeaderValue::from_str(&joined) {
            response.headers_mut().insert("x-kura-abuse-signals", value);
        }
    }
    if let Some(cooldown_until) = decision.cooldown_until
        && let Ok(value) = HeaderValue::from_str(&cooldown_until.to_rfc3339())
    {
        response
            .headers_mut()
            .insert("x-kura-abuse-cooldown-until", value);
    }
}

fn should_persist_adaptive_telemetry(
    assessment: &AbuseRiskAssessment,
    decision: &AbuseDecision,
    allow_sampled: bool,
) -> bool {
    decision.action != AbuseAction::Allow
        || decision.recovered_from_cooldown
        || !assessment.signals.is_empty()
        || allow_sampled
}

async fn persist_adaptive_abuse_telemetry(
    pool: &sqlx::PgPool,
    record: AdaptiveAbuseTelemetryRecord,
) {
    if let Err(err) = sqlx::query(
        r#"
        INSERT INTO security_abuse_telemetry (
            user_id,
            profile,
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
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18
        )
        "#,
    )
    .bind(record.user_id)
    .bind(record.profile.as_str())
    .bind(record.path)
    .bind(record.method)
    .bind(action_label(record.action))
    .bind(record.risk_score)
    .bind(record.cooldown_active)
    .bind(record.cooldown_until)
    .bind(record.total_requests_60s)
    .bind(record.denied_requests_60s)
    .bind(record.unique_paths_60s)
    .bind(record.context_reads_60s)
    .bind(record.denied_ratio_60s)
    .bind(record.signals)
    .bind(record.false_positive_hint)
    .bind(record.ux_impact_hint)
    .bind(record.response_status_code)
    .bind(record.response_time_ms)
    .execute(pool)
    .await
    {
        tracing::warn!(
            error = %err,
            user_id = %record.user_id,
            "failed to persist adaptive abuse telemetry"
        );
    }
}

fn action_label(action: AbuseAction) -> &'static str {
    match action {
        AbuseAction::Allow => "allow",
        AbuseAction::Throttle => "throttle",
        AbuseAction::Block => "block",
        AbuseAction::Recovery => "recovery",
    }
}

#[cfg(test)]
mod tests {
    use super::{
        AbuseAction, AbuseRiskAssessment, AccessSignalSnapshot, apply_cooldown_policy,
        evaluate_abuse_risk, normalize_agent_path_for_signals, profile_tuning,
    };
    use crate::security_profile::SecurityProfile;
    use chrono::{Duration as ChronoDuration, Utc};
    use std::collections::HashMap;
    use std::sync::Arc;
    use tokio::sync::RwLock;
    use uuid::Uuid;

    #[test]
    fn normalize_agent_path_collapses_event_id_tail() {
        assert_eq!(
            normalize_agent_path_for_signals("/v1/agent/evidence/event/1234"),
            "/v1/agent/evidence/event/{event_id}"
        );
        assert_eq!(
            normalize_agent_path_for_signals("/v1/agent/context"),
            "/v1/agent/context"
        );
    }

    #[test]
    fn risk_scoring_triggers_throttle_for_context_scrape_signal() {
        let snapshot = AccessSignalSnapshot {
            total_requests_60s: 26,
            denied_requests_60s: 0,
            denied_authz_requests_60s: 0,
            denied_not_found_requests_60s: 0,
            unique_paths_60s: 2,
            context_reads_60s: 9,
            write_requests_60s: 0,
        };
        let tuning = profile_tuning(SecurityProfile::Adaptive);
        let assessment = evaluate_abuse_risk(&snapshot, SecurityProfile::Adaptive);
        assert!(assessment.score >= tuning.throttle_score_threshold);
        assert_eq!(assessment.base_action, AbuseAction::Throttle);
        assert!(
            assessment
                .signals
                .iter()
                .any(|signal| signal == "context_scrape_burst_60s")
        );
    }

    #[test]
    fn risk_scoring_triggers_block_for_multi_signal_abuse() {
        let snapshot = AccessSignalSnapshot {
            total_requests_60s: 38,
            denied_requests_60s: 20,
            denied_authz_requests_60s: 20,
            denied_not_found_requests_60s: 0,
            unique_paths_60s: 8,
            context_reads_60s: 10,
            write_requests_60s: 13,
        };
        let tuning = profile_tuning(SecurityProfile::Adaptive);
        let assessment = evaluate_abuse_risk(&snapshot, SecurityProfile::Adaptive);
        assert!(assessment.score >= tuning.block_score_threshold);
        assert_eq!(assessment.base_action, AbuseAction::Block);
    }

    #[test]
    fn risk_scoring_returns_allow_when_signals_absent() {
        let snapshot = AccessSignalSnapshot {
            total_requests_60s: 4,
            denied_requests_60s: 0,
            denied_authz_requests_60s: 0,
            denied_not_found_requests_60s: 0,
            unique_paths_60s: 2,
            context_reads_60s: 1,
            write_requests_60s: 0,
        };
        let assessment = evaluate_abuse_risk(&snapshot, SecurityProfile::Adaptive);
        assert_eq!(assessment.score, 0);
        assert_eq!(assessment.base_action, AbuseAction::Allow);
        assert!(assessment.signals.is_empty());
    }

    #[test]
    fn default_profile_remains_low_friction_for_mild_risk() {
        let snapshot = AccessSignalSnapshot {
            total_requests_60s: 26,
            denied_requests_60s: 2,
            denied_authz_requests_60s: 2,
            denied_not_found_requests_60s: 0,
            unique_paths_60s: 3,
            context_reads_60s: 8,
            write_requests_60s: 0,
        };
        let assessment_default = evaluate_abuse_risk(&snapshot, SecurityProfile::Default);
        let assessment_adaptive = evaluate_abuse_risk(&snapshot, SecurityProfile::Adaptive);
        assert_eq!(assessment_default.base_action, AbuseAction::Allow);
        assert_eq!(assessment_adaptive.base_action, AbuseAction::Throttle);
    }

    #[tokio::test]
    async fn cooldown_keeps_throttle_while_window_is_active() {
        let user_id = Uuid::now_v7();
        let now = Utc::now();
        let state = Arc::new(RwLock::new(HashMap::new()));
        {
            let mut lock = state.write().await;
            lock.insert(
                user_id,
                super::CooldownState {
                    cooldown_until: now + ChronoDuration::seconds(30),
                },
            );
        }
        let assessment = AbuseRiskAssessment {
            snapshot: AccessSignalSnapshot::default(),
            profile: SecurityProfile::Adaptive,
            score: 0,
            signals: Vec::new(),
            base_action: AbuseAction::Allow,
        };

        let decision = apply_cooldown_policy(&state, user_id, &assessment, now).await;
        assert_eq!(decision.action, AbuseAction::Throttle);
        assert!(decision.cooldown_active);
    }

    #[test]
    fn denied_ratio_signal_is_tempered_for_small_samples() {
        let snapshot = AccessSignalSnapshot {
            total_requests_60s: 10,
            denied_requests_60s: 4,
            denied_authz_requests_60s: 2,
            denied_not_found_requests_60s: 2,
            unique_paths_60s: 2,
            context_reads_60s: 0,
            write_requests_60s: 0,
        };
        let assessment = evaluate_abuse_risk(&snapshot, SecurityProfile::Adaptive);
        assert!(
            !assessment
                .signals
                .iter()
                .any(|signal| signal == "denied_ratio_spike_60s"),
            "small sample with mixed 404s should not trigger denied-ratio spike"
        );
        assert_eq!(assessment.base_action, AbuseAction::Allow);
    }

    #[test]
    fn denied_ratio_high_volume_triggers_high_confidence_signal() {
        let snapshot = AccessSignalSnapshot {
            total_requests_60s: 40,
            denied_requests_60s: 30,
            denied_authz_requests_60s: 28,
            denied_not_found_requests_60s: 2,
            unique_paths_60s: 3,
            context_reads_60s: 0,
            write_requests_60s: 0,
        };
        let assessment = evaluate_abuse_risk(&snapshot, SecurityProfile::Adaptive);
        assert!(
            assessment
                .signals
                .iter()
                .any(|signal| signal == "denied_ratio_high_confidence_60s")
        );
        assert!(assessment.score >= profile_tuning(SecurityProfile::Adaptive).throttle_score_threshold);
    }

    #[tokio::test]
    async fn cooldown_recovery_clears_state_when_expired() {
        let user_id = Uuid::now_v7();
        let now = Utc::now();
        let state = Arc::new(RwLock::new(HashMap::new()));
        {
            let mut lock = state.write().await;
            lock.insert(
                user_id,
                super::CooldownState {
                    cooldown_until: now - ChronoDuration::seconds(1),
                },
            );
        }
        let assessment = AbuseRiskAssessment {
            snapshot: AccessSignalSnapshot::default(),
            profile: SecurityProfile::Adaptive,
            score: 0,
            signals: Vec::new(),
            base_action: AbuseAction::Allow,
        };

        let decision = apply_cooldown_policy(&state, user_id, &assessment, now).await;
        assert_eq!(decision.action, AbuseAction::Allow);
        assert!(!decision.cooldown_active);
        assert!(decision.recovered_from_cooldown);
    }
}
