use super::*;

pub(super) const AGENT_CAPABILITIES_SCHEMA_VERSION: &str = "agent_capabilities.v2.self_model";
pub(super) const AGENT_CONTEXT_CONTRACT_VERSION: &str = "agent_context.v12.brief_first.overflow_protocol.temporal_grounding.challenge_memory.decision_brief_detail_caps.consent_gate.budgeted_payload.objective_surfaces";
pub(super) const AGENT_CONTEXT_SYSTEM_CONTRACT_VERSION: &str = "agent_context.system.v1";
pub(super) const AGENT_CONTEXT_SYSTEM_PROFILE: &str = "redacted_v1";
pub(super) const AGENT_BRIEF_SCHEMA_VERSION: &str = "agent_brief.v1";
pub(super) const AGENT_TEMPORAL_CONTEXT_SCHEMA_VERSION: &str = "temporal_context.v1";
pub(super) const AGENT_CONSENT_WRITE_GATE_SCHEMA_VERSION: &str = "consent_write_gate.v1";
pub(super) const AGENT_HEALTH_CONSENT_ERROR_CODE: &str = "health_consent_required";
pub(super) const AGENT_HEALTH_CONSENT_NEXT_ACTION: &str = "open_settings_privacy";
pub(super) const AGENT_HEALTH_CONSENT_SETTINGS_URL: &str = "/settings?section=privacy";
pub(super) const AGENT_TEMPORAL_BASIS_SCHEMA_VERSION: &str = "temporal_basis.v1";
pub(super) const AGENT_TEMPORAL_BASIS_MAX_AGE_MINUTES: i64 = 45;
pub(super) const AGENT_TEMPORAL_BASIS_MAX_FUTURE_SKEW_MINUTES: i64 = 2;
pub(super) const AGENT_DEFAULT_ASSUMED_TIMEZONE: &str = "UTC";
pub(super) const AGENT_TIMEZONE_ASSUMPTION_DISCLOSURE: &str =
    "No explicit timezone preference found; using UTC until the user confirms one.";
pub(super) const AGENT_CHALLENGE_MODE_SCHEMA_VERSION: &str = "challenge_mode.v1";
pub(super) const AGENT_CHALLENGE_MODE_ONBOARDING_HINT: &str = "Challenge Mode ist standardmäßig auf auto aktiv. Sag 'Challenge Mode aus', wenn ich weniger challengen soll.";
pub(super) const AGENT_MEMORY_TIER_CONTRACT_VERSION: &str = "memory_tier_contract.v1";
pub(super) const AGENT_SELF_MODEL_SCHEMA_VERSION: &str = "agent_self_model.v1";
pub(super) const MODEL_TIER_REGISTRY_VERSION: &str = "model_tier_registry_v1";
pub(super) const MODEL_ATTESTATION_SCHEMA_VERSION: &str = "model_attestation.v1";
pub(super) const HIGH_IMPACT_CONFIRMATION_SCHEMA_VERSION: &str = "high_impact_confirmation.v1";
pub(super) const INTENT_HANDSHAKE_SCHEMA_VERSION: &str = "intent_handshake.v1";
pub(super) const INTENT_HANDSHAKE_MAX_AGE_MINUTES: i64 = 45;
pub(super) const TRACE_DIGEST_SCHEMA_VERSION: &str = "trace_digest.v1";
pub(super) const POST_TASK_REFLECTION_SCHEMA_VERSION: &str = "post_task_reflection.v1";
pub(super) const RESPONSE_MODE_POLICY_SCHEMA_VERSION: &str = "response_mode_policy.v1";
pub(super) const PERSONAL_FAILURE_PROFILE_SCHEMA_VERSION: &str = "personal_failure_profile.v1";
pub(super) const RETRIEVAL_REGRET_SCHEMA_VERSION: &str = "retrieval_regret.v1";
pub(super) const LAAJ_SIDECAR_SCHEMA_VERSION: &str = "laaj_sidecar.v1";
pub(super) const COUNTERFACTUAL_RECOMMENDATION_SCHEMA_VERSION: &str =
    "counterfactual_recommendation.v1";
pub(super) const ADVISORY_SCORING_LAYER_SCHEMA_VERSION: &str = "advisory_scoring_layer.v1";
pub(super) const ADVISORY_ACTION_PLAN_SCHEMA_VERSION: &str = "advisory_action_plan.v1";
pub(super) const ADVISORY_RESPONSE_HINT_GROUNDED_MIN_SPECIFICITY: f64 = 0.72;
pub(super) const ADVISORY_RESPONSE_HINT_GROUNDED_MAX_HALLUCINATION_RISK: f64 = 0.40;
pub(super) const ADVISORY_RESPONSE_HINT_GROUNDED_MAX_DATA_QUALITY_RISK: f64 = 0.42;
pub(super) const ADVISORY_RESPONSE_HINT_GENERAL_MIN_HALLUCINATION_RISK: f64 = 0.65;
pub(super) const ADVISORY_RESPONSE_HINT_GENERAL_MAX_CONFIDENCE: f64 = 0.45;
pub(super) const ADVISORY_RESPONSE_HINT_GENERAL_MIN_DATA_QUALITY_RISK: f64 = 0.62;
pub(super) const ADVISORY_PERSIST_ACTION_ASK_FIRST_MIN_RISK: f64 = 0.72;
pub(super) const ADVISORY_PERSIST_ACTION_DRAFT_MIN_RISK: f64 = 0.48;
pub(super) const ADVISORY_CLARIFICATION_BUDGET_MIN_RISK: f64 = 0.55;
pub(super) const ADVISORY_UNCERTAINTY_NOTE_MIN_HALLUCINATION_RISK: f64 = 0.45;
pub(super) const ADVISORY_UNCERTAINTY_NOTE_MAX_CONFIDENCE: f64 = 0.62;
pub(super) const DECISION_BRIEF_SCHEMA_VERSION: &str = "decision_brief.v1";
pub(super) const RESPONSE_MODE_POLICY_ROLE_NUDGE_ONLY: &str = "nudge_only";
pub(super) const SIDECAR_POLICY_ROLE_ADVISORY_ONLY: &str = "advisory_only";
pub(super) const RESPONSE_MODE_INVARIANT_ID: &str = "INV-010";
pub(super) const PERSONAL_FAILURE_PROFILE_INVARIANT_ID: &str = "INV-011";
pub(super) const RETRIEVAL_REGRET_INVARIANT_ID: &str = "INV-012";
pub(super) const LAAJ_SIDECAR_INVARIANT_ID: &str = "INV-013";
pub(super) const COUNTERFACTUAL_RECOMMENDATION_INVARIANT_ID: &str = "INV-014";
pub(super) const ADVISORY_SCORING_INVARIANT_ID: &str = "INV-015";
pub(super) const MODEL_IDENTITY_UNKNOWN_FALLBACK_REASON_CODE: &str =
    "model_identity_unknown_fallback_strict";
pub(super) const MODEL_ATTESTATION_MISSING_REASON_CODE: &str = "model_attestation_missing_fallback";
pub(super) const MODEL_ATTESTATION_INVALID_SCHEMA_REASON_CODE: &str =
    "model_attestation_invalid_schema";
pub(super) const MODEL_ATTESTATION_INVALID_DIGEST_REASON_CODE: &str =
    "model_attestation_invalid_request_digest";
pub(super) const MODEL_ATTESTATION_INVALID_SIGNATURE_REASON_CODE: &str =
    "model_attestation_invalid_signature";
pub(super) const MODEL_ATTESTATION_STALE_REASON_CODE: &str = "model_attestation_stale";
pub(super) const MODEL_ATTESTATION_REPLAY_REASON_CODE: &str = "model_attestation_replayed";
pub(super) const MODEL_ATTESTATION_MALFORMED_REASON_CODE: &str = "model_attestation_malformed";
pub(super) const MODEL_ATTESTATION_SECRET_UNCONFIGURED_REASON_CODE: &str =
    "model_attestation_secret_unconfigured";
pub(super) const MODEL_TIER_AUTO_LOW_SAMPLES_CONFIRM_REASON_CODE: &str =
    "model_tier_auto_low_samples_confirm_first";
pub(super) const MODEL_TIER_AUTO_STRICT_REASON_CODE: &str = "model_tier_auto_quality_strict";
pub(super) const MODEL_TIER_STRICT_CONFIRM_REASON_CODE: &str =
    "model_tier_strict_requires_confirmation";
pub(super) const MODEL_TIER_CONFIRM_REASON_CODE: &str = "model_tier_requires_confirmation";
pub(super) const CALIBRATION_MONITOR_CONFIRM_REASON_CODE: &str =
    "calibration_monitor_requires_confirmation";
pub(super) const INTEGRITY_MONITOR_CONFIRM_REASON_CODE: &str =
    "integrity_monitor_requires_confirmation";
pub(super) const CALIBRATION_DEGRADED_CONFIRM_REASON_CODE: &str =
    "calibration_degraded_requires_confirmation";
pub(super) const INTEGRITY_DEGRADED_CONFIRM_REASON_CODE: &str =
    "integrity_degraded_requires_confirmation";
pub(super) const HIGH_IMPACT_CONFIRMATION_REQUIRED_REASON_CODE: &str =
    "high_impact_confirmation_required";
pub(super) const HIGH_IMPACT_CONFIRMATION_INVALID_REASON_CODE: &str =
    "high_impact_confirmation_invalid";
pub(super) const HIGH_IMPACT_CONFIRMATION_TOKEN_MISSING_REASON_CODE: &str =
    "high_impact_confirmation_token_missing";
pub(super) const HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE: &str =
    "high_impact_confirmation_token_invalid";
pub(super) const HIGH_IMPACT_CONFIRMATION_TOKEN_STALE_REASON_CODE: &str =
    "high_impact_confirmation_token_stale";
pub(super) const HIGH_IMPACT_CONFIRMATION_PAYLOAD_MISMATCH_REASON_CODE: &str =
    "high_impact_confirmation_payload_mismatch";
pub(super) const HIGH_IMPACT_CONFIRMATION_SECRET_UNCONFIGURED_REASON_CODE: &str =
    "high_impact_confirmation_secret_unconfigured";
pub(super) const USER_CONFIRMATION_STRICTNESS_ALWAYS_REASON_CODE: &str =
    "user_confirmation_strictness_always";
pub(super) const MEMORY_TIER_PRINCIPLES_STALE_CONFIRM_REASON_CODE: &str =
    "memory_principles_stale_confirm_first";
pub(super) const MEMORY_TIER_PRINCIPLES_MISSING_CONFIRM_REASON_CODE: &str =
    "memory_principles_missing_confirm_first";
pub(super) const MODEL_ATTESTATION_SECRET_ENV: &str = "KURA_AGENT_MODEL_ATTESTATION_SECRET";
pub(super) const KURA_AGENT_MODEL_IDENTITY_ENV: &str = "KURA_AGENT_MODEL_IDENTITY";
pub(super) const KURA_AGENT_MODEL_BY_CLIENT_ID_ENV: &str = "KURA_AGENT_MODEL_BY_CLIENT_ID_JSON";
pub(super) const KURA_AGENT_DEVELOPER_RAW_USER_ALLOWLIST_ENV: &str =
    "KURA_AGENT_DEVELOPER_RAW_USER_ALLOWLIST";
pub(super) const AGENT_LANGUAGE_MODE_HEADER: &str = "x-kura-debug-language-mode";
pub(super) const MODEL_ATTESTATION_MAX_AGE_SECONDS: i64 = 300;
pub(super) const MODEL_ATTESTATION_MAX_FUTURE_SKEW_SECONDS: i64 = 30;
pub(super) const MODEL_TIER_AUTO_LOOKBACK_DAYS: i64 = 30;
pub(super) const MODEL_TIER_AUTO_MIN_SAMPLES: i64 = 12;
pub(super) const MODEL_TIER_AUTO_ADVANCED_MAX_MISMATCH_PCT: f64 = 0.60;
pub(super) const MODEL_TIER_AUTO_MODERATE_MAX_MISMATCH_PCT: f64 = 4.00;
pub(super) const MODEL_TIER_AUTO_ADVANCED_PROMOTE_PCT: f64 = 0.40;
pub(super) const MODEL_TIER_AUTO_ADVANCED_DEMOTE_PCT: f64 = 1.50;
pub(super) const MODEL_TIER_AUTO_STRICT_ENTER_PCT: f64 = 6.00;
pub(super) const MODEL_TIER_AUTO_STRICT_EXIT_PCT: f64 = 2.00;
pub(super) const HIGH_IMPACT_CONFIRMATION_MAX_AGE_MINUTES: i64 = 45;
pub(super) const HIGH_IMPACT_CONFIRMATION_MAX_FUTURE_SKEW_MINUTES: i64 = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum AgentLanguageMode {
    UserSafe,
    DeveloperRaw,
}

// Model tier is determined by auto-tiering (observed quality), not by model name.
// All models start at moderate and earn advancement through performance.
// Model identity is retained for audit/logging and quality track separation only.

#[derive(Debug, Clone)]
pub(super) struct ResolvedModelIdentity {
    pub(super) model_identity: String,
    pub(super) reason_codes: Vec<String>,
    pub(super) source: String,
    pub(super) attestation_request_id: Option<String>,
}

#[derive(Debug, Clone)]
pub(super) struct VerifiedModelAttestation {
    pub(super) model_identity: String,
    pub(super) request_id: String,
}

#[derive(sqlx::FromRow)]
pub(super) struct ModelTierTelemetryRow {
    sample_count: i64,
    mismatch_weighted_sum: f64,
}

pub(super) type HmacSha256 = Hmac<Sha256>;

pub(super) static MODEL_ATTESTATION_NONCES: LazyLock<Mutex<HashMap<String, DateTime<Utc>>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

#[derive(Debug, Clone, Copy)]
pub(super) struct ModelTierPolicy {
    pub(super) registry_version: &'static str,
    pub(super) capability_tier: &'static str,
    pub(super) confidence_floor: f64,
    pub(super) allowed_action_scope: &'static str,
    pub(super) high_impact_write_policy: &'static str,
    pub(super) repair_auto_apply_cap: &'static str,
}

pub(super) fn normalize_model_identity(raw: &str) -> Option<String> {
    let normalized = raw.trim().to_lowercase();
    if normalized.is_empty() {
        None
    } else {
        Some(normalized)
    }
}

pub(super) fn resolve_model_identity_from_client_map(
    client_id: &str,
    raw_map: &str,
) -> Option<String> {
    let Value::Object(map) = serde_json::from_str::<Value>(raw_map).ok()? else {
        return None;
    };
    let normalized_client_id = client_id.trim().to_lowercase();
    for (candidate_client_id, value) in map {
        if !candidate_client_id
            .trim()
            .eq_ignore_ascii_case(&normalized_client_id)
        {
            continue;
        }
        if let Some(model_identity) = value.as_str() {
            return normalize_model_identity(model_identity);
        }
    }
    None
}

pub(super) fn resolve_model_identity_with_sources(
    client_id: Option<&str>,
    client_map_json: Option<&str>,
    runtime_default_identity: Option<&str>,
) -> ResolvedModelIdentity {
    if let (Some(client_id), Some(client_map_json)) = (client_id, client_map_json) {
        if let Some(model_identity) =
            resolve_model_identity_from_client_map(client_id, client_map_json)
        {
            return ResolvedModelIdentity {
                model_identity,
                reason_codes: Vec::new(),
                source: "client_map".to_string(),
                attestation_request_id: None,
            };
        }
    }

    if let Some(runtime_identity) = runtime_default_identity.and_then(normalize_model_identity) {
        return ResolvedModelIdentity {
            model_identity: runtime_identity,
            reason_codes: Vec::new(),
            source: "runtime_default".to_string(),
            attestation_request_id: None,
        };
    }

    ResolvedModelIdentity {
        model_identity: "unknown".to_string(),
        reason_codes: vec![MODEL_IDENTITY_UNKNOWN_FALLBACK_REASON_CODE.to_string()],
        source: "unknown_fallback".to_string(),
        attestation_request_id: None,
    }
}

pub(super) fn resolve_model_identity(auth: &AuthenticatedUser) -> ResolvedModelIdentity {
    let client_id = match &auth.auth_method {
        AuthMethod::AccessToken { client_id, .. } => Some(client_id.as_str()),
        AuthMethod::ApiKey { .. } => None,
    };
    let client_map_json = std::env::var(KURA_AGENT_MODEL_BY_CLIENT_ID_ENV).ok();
    let runtime_default_identity = std::env::var(KURA_AGENT_MODEL_IDENTITY_ENV).ok();
    resolve_model_identity_with_sources(
        client_id,
        client_map_json.as_deref(),
        runtime_default_identity.as_deref(),
    )
}

pub(super) fn canonical_model_attestation_issued_at(issued_at: DateTime<Utc>) -> String {
    issued_at.to_rfc3339_opts(SecondsFormat::Secs, true)
}

pub(super) fn build_write_request_digest(
    req: &AgentWriteWithProofRequest,
    action_class: &str,
    include_high_impact_confirmation: bool,
) -> String {
    let events = req
        .events
        .iter()
        .map(|event| {
            serde_json::json!({
                "timestamp": event.timestamp.to_rfc3339(),
                "event_type": event.event_type,
                "data": event.data,
                "metadata": {
                    "source": event.metadata.source,
                    "agent": event.metadata.agent,
                    "device": event.metadata.device,
                    "session_id": event.metadata.session_id,
                    "idempotency_key": event.metadata.idempotency_key,
                },
            })
        })
        .collect::<Vec<_>>();
    let targets = req
        .read_after_write_targets
        .iter()
        .map(|target| {
            serde_json::json!({
                "projection_type": target.projection_type,
                "key": target.key,
            })
        })
        .collect::<Vec<_>>();
    let mut payload = serde_json::Map::new();
    payload.insert("events".to_string(), Value::Array(events));
    payload.insert(
        "read_after_write_targets".to_string(),
        Value::Array(targets),
    );
    payload.insert(
        "verify_timeout_ms".to_string(),
        json!(req.verify_timeout_ms),
    );
    payload.insert(
        "include_repair_technical_details".to_string(),
        json!(req.include_repair_technical_details),
    );
    payload.insert("intent_handshake".to_string(), json!(req.intent_handshake));
    payload.insert("action_class".to_string(), json!(action_class));
    if include_high_impact_confirmation {
        payload.insert(
            "high_impact_confirmation".to_string(),
            json!(req.high_impact_confirmation),
        );
    }
    let serialized =
        serde_json::to_string(&Value::Object(payload)).unwrap_or_else(|_| "{}".to_string());
    stable_hash_suffix(&serialized, 64)
}

pub(super) fn build_model_attestation_request_digest(
    req: &AgentWriteWithProofRequest,
    action_class: &str,
) -> String {
    build_write_request_digest(req, action_class, true)
}

pub(super) fn build_high_impact_confirmation_request_digest(
    req: &AgentWriteWithProofRequest,
    action_class: &str,
) -> String {
    build_write_request_digest(req, action_class, false)
}

pub(super) fn normalize_hex_64(raw: &str) -> Option<String> {
    let normalized = raw.trim().to_lowercase();
    if normalized.len() == 64 && normalized.chars().all(|ch| ch.is_ascii_hexdigit()) {
        Some(normalized)
    } else {
        None
    }
}

pub(super) fn compute_model_attestation_signature(
    secret: &str,
    model_identity: &str,
    issued_at: DateTime<Utc>,
    request_id: &str,
    request_digest: &str,
    user_id: Uuid,
) -> Option<String> {
    let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).ok()?;
    let payload = format!(
        "{}|{}|{}|{}|{}|{}",
        MODEL_ATTESTATION_SCHEMA_VERSION,
        model_identity.trim().to_lowercase(),
        canonical_model_attestation_issued_at(issued_at),
        request_id.trim(),
        request_digest.trim().to_lowercase(),
        user_id
    );
    mac.update(payload.as_bytes());
    Some(hex::encode(mac.finalize().into_bytes()))
}

pub(super) fn compute_high_impact_confirmation_token_signature(
    secret: &str,
    user_id: Uuid,
    action_class: &str,
    request_digest: &str,
    issued_at: DateTime<Utc>,
) -> Option<String> {
    let digest = normalize_hex_64(request_digest)?;
    let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).ok()?;
    let payload = format!(
        "{}|{}|{}|{}|{}",
        HIGH_IMPACT_CONFIRMATION_SCHEMA_VERSION,
        user_id,
        action_class.trim().to_lowercase(),
        digest,
        canonical_model_attestation_issued_at(issued_at),
    );
    mac.update(payload.as_bytes());
    Some(hex::encode(mac.finalize().into_bytes()))
}

pub(super) fn issue_high_impact_confirmation_token(
    secret: &str,
    user_id: Uuid,
    action_class: &str,
    request_digest: &str,
    issued_at: DateTime<Utc>,
) -> Option<String> {
    let digest = normalize_hex_64(request_digest)?;
    let signature = compute_high_impact_confirmation_token_signature(
        secret,
        user_id,
        action_class,
        &digest,
        issued_at,
    )?;
    Some(format!(
        "v1|{}|{}|{}",
        canonical_model_attestation_issued_at(issued_at),
        digest,
        signature
    ))
}

pub(super) fn normalize_attestation_signature(signature: &str) -> Option<String> {
    let trimmed = signature.trim().to_lowercase();
    if trimmed.is_empty() {
        return None;
    }
    let normalized = trimmed
        .strip_prefix("sha256=")
        .unwrap_or(trimmed.as_str())
        .to_string();
    if normalized.len() != 64 || !normalized.chars().all(|ch| ch.is_ascii_hexdigit()) {
        return None;
    }
    Some(normalized)
}

pub(super) fn consume_model_attestation_nonce(request_id: &str, now: DateTime<Utc>) -> bool {
    let mut cache = MODEL_ATTESTATION_NONCES
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    let retention = chrono::Duration::seconds(MODEL_ATTESTATION_MAX_AGE_SECONDS * 4);
    cache.retain(|_, seen_at| *seen_at + retention >= now);

    if cache.contains_key(request_id) {
        return false;
    }
    cache.insert(request_id.to_string(), now);
    true
}

#[cfg(test)]
pub(super) fn clear_model_attestation_nonce_cache() {
    MODEL_ATTESTATION_NONCES
        .lock()
        .unwrap_or_else(|poison| poison.into_inner())
        .clear();
}

pub(super) fn verify_model_attestation(
    attestation: &AgentModelAttestation,
    expected_request_digest: &str,
    user_id: Uuid,
    now: DateTime<Utc>,
    secret: Option<&str>,
) -> Result<VerifiedModelAttestation, Vec<String>> {
    let mut reason_codes = Vec::new();

    if attestation.schema_version.trim() != MODEL_ATTESTATION_SCHEMA_VERSION {
        reason_codes.push(MODEL_ATTESTATION_INVALID_SCHEMA_REASON_CODE.to_string());
    }
    let Some(model_identity) = normalize_model_identity(&attestation.runtime_model_identity) else {
        reason_codes.push(MODEL_ATTESTATION_MALFORMED_REASON_CODE.to_string());
        return Err(reason_codes);
    };

    let request_id = attestation.request_id.trim();
    if request_id.is_empty() || request_id.len() > 256 {
        reason_codes.push(MODEL_ATTESTATION_MALFORMED_REASON_CODE.to_string());
    }

    let digest = attestation.request_digest.trim().to_lowercase();
    if digest.is_empty() || digest != expected_request_digest.trim().to_lowercase() {
        reason_codes.push(MODEL_ATTESTATION_INVALID_DIGEST_REASON_CODE.to_string());
    }

    let age = now.signed_duration_since(attestation.issued_at);
    if age > chrono::Duration::seconds(MODEL_ATTESTATION_MAX_AGE_SECONDS)
        || age < chrono::Duration::seconds(-MODEL_ATTESTATION_MAX_FUTURE_SKEW_SECONDS)
    {
        reason_codes.push(MODEL_ATTESTATION_STALE_REASON_CODE.to_string());
    }

    let Some(secret_value) = secret.and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed)
        }
    }) else {
        reason_codes.push(MODEL_ATTESTATION_SECRET_UNCONFIGURED_REASON_CODE.to_string());
        dedupe_reason_codes(&mut reason_codes);
        return Err(reason_codes);
    };

    let Some(expected_signature) = compute_model_attestation_signature(
        secret_value,
        &model_identity,
        attestation.issued_at,
        request_id,
        &digest,
        user_id,
    ) else {
        reason_codes.push(MODEL_ATTESTATION_MALFORMED_REASON_CODE.to_string());
        dedupe_reason_codes(&mut reason_codes);
        return Err(reason_codes);
    };

    let provided_signature = normalize_attestation_signature(&attestation.signature);
    if provided_signature.as_deref() != Some(expected_signature.as_str()) {
        reason_codes.push(MODEL_ATTESTATION_INVALID_SIGNATURE_REASON_CODE.to_string());
    }

    if !consume_model_attestation_nonce(request_id, now) {
        reason_codes.push(MODEL_ATTESTATION_REPLAY_REASON_CODE.to_string());
    }

    dedupe_reason_codes(&mut reason_codes);
    if !reason_codes.is_empty() {
        return Err(reason_codes);
    }

    Ok(VerifiedModelAttestation {
        model_identity,
        request_id: request_id.to_string(),
    })
}

pub(super) fn resolve_model_identity_for_write(
    auth: &AuthenticatedUser,
    req: &AgentWriteWithProofRequest,
    action_class: &str,
    now: DateTime<Utc>,
) -> ResolvedModelIdentity {
    let request_digest = build_model_attestation_request_digest(req, action_class);
    let attestation_secret = std::env::var(MODEL_ATTESTATION_SECRET_ENV).ok();
    if let Some(attestation) = req.model_attestation.as_ref() {
        return match verify_model_attestation(
            attestation,
            &request_digest,
            auth.user_id,
            now,
            attestation_secret.as_deref(),
        ) {
            Ok(verified) => ResolvedModelIdentity {
                model_identity: verified.model_identity,
                reason_codes: Vec::new(),
                source: "attested_runtime".to_string(),
                attestation_request_id: Some(verified.request_id),
            },
            Err(mut reason_codes) => {
                reason_codes.push(MODEL_IDENTITY_UNKNOWN_FALLBACK_REASON_CODE.to_string());
                dedupe_reason_codes(&mut reason_codes);
                ResolvedModelIdentity {
                    model_identity: "unknown".to_string(),
                    reason_codes,
                    source: "attestation_invalid".to_string(),
                    attestation_request_id: None,
                }
            }
        };
    }

    let mut fallback = resolve_model_identity(auth);
    if fallback
        .reason_codes
        .iter()
        .any(|code| code == MODEL_IDENTITY_UNKNOWN_FALLBACK_REASON_CODE)
    {
        fallback
            .reason_codes
            .push(MODEL_ATTESTATION_MISSING_REASON_CODE.to_string());
        dedupe_reason_codes(&mut fallback.reason_codes);
    }
    fallback
}

pub(super) fn verify_high_impact_confirmation_token(
    token: &str,
    secret: &str,
    user_id: Uuid,
    action_class: &str,
    expected_request_digest: &str,
    now: DateTime<Utc>,
) -> Result<(), Vec<String>> {
    let mut reason_codes = Vec::new();
    let mut parts = token.trim().split('|');
    let Some(version) = parts.next() else {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE.to_string());
        return Err(reason_codes);
    };
    let Some(issued_at_raw) = parts.next() else {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE.to_string());
        return Err(reason_codes);
    };
    let Some(digest_raw) = parts.next() else {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE.to_string());
        return Err(reason_codes);
    };
    let Some(signature_raw) = parts.next() else {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE.to_string());
        return Err(reason_codes);
    };
    if parts.next().is_some() || version != "v1" {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE.to_string());
        return Err(reason_codes);
    }

    let issued_at =
        match DateTime::parse_from_rfc3339(issued_at_raw).map(|value| value.with_timezone(&Utc)) {
            Ok(value) => value,
            Err(_) => {
                reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE.to_string());
                return Err(reason_codes);
            }
        };
    let Some(token_digest) = normalize_hex_64(digest_raw) else {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE.to_string());
        return Err(reason_codes);
    };
    let Some(expected_digest) = normalize_hex_64(expected_request_digest) else {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE.to_string());
        return Err(reason_codes);
    };
    if token_digest != expected_digest {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_PAYLOAD_MISMATCH_REASON_CODE.to_string());
    }

    let age = now.signed_duration_since(issued_at);
    if age > chrono::Duration::minutes(HIGH_IMPACT_CONFIRMATION_MAX_AGE_MINUTES)
        || age < chrono::Duration::minutes(-HIGH_IMPACT_CONFIRMATION_MAX_FUTURE_SKEW_MINUTES)
    {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_STALE_REASON_CODE.to_string());
    }

    let provided_signature = normalize_attestation_signature(signature_raw);
    let expected_signature = compute_high_impact_confirmation_token_signature(
        secret,
        user_id,
        action_class,
        &token_digest,
        issued_at,
    );
    if expected_signature.is_none()
        || provided_signature.as_deref() != expected_signature.as_deref()
    {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_INVALID_REASON_CODE.to_string());
    }

    dedupe_reason_codes(&mut reason_codes);
    if reason_codes.is_empty() {
        Ok(())
    } else {
        Err(reason_codes)
    }
}

pub(super) fn header_requests_developer_raw(mode_header: Option<&str>) -> bool {
    let Some(raw) = mode_header else {
        return false;
    };
    matches!(
        raw.trim().to_lowercase().as_str(),
        "raw" | "developer_raw" | "developer-raw" | "off"
    )
}

pub(super) fn header_requests_user_safe(mode_header: Option<&str>) -> bool {
    let Some(raw) = mode_header else {
        return false;
    };
    matches!(
        raw.trim().to_lowercase().as_str(),
        "safe" | "user_safe" | "user-safe" | "on"
    )
}

pub(super) fn is_allowlisted_developer_raw_user(user_id: Uuid, allowlist: Option<&str>) -> bool {
    let normalized_user_id = user_id.to_string().to_lowercase();
    let Some(raw_allowlist) = allowlist else {
        return false;
    };
    raw_allowlist
        .split(',')
        .map(|entry| entry.trim().to_lowercase())
        .any(|entry| entry == "*" || entry == normalized_user_id)
}

pub(super) fn resolve_agent_language_mode_with_sources(
    auth: &AuthenticatedUser,
    mode_header: Option<&str>,
    allowlist: Option<&str>,
) -> AgentLanguageMode {
    let allowlisted_user = is_allowlisted_developer_raw_user(auth.user_id, allowlist);
    if allowlisted_user {
        if header_requests_user_safe(mode_header) {
            return AgentLanguageMode::UserSafe;
        }
        AgentLanguageMode::DeveloperRaw
    } else {
        AgentLanguageMode::UserSafe
    }
}

pub(super) fn resolve_agent_language_mode(
    auth: &AuthenticatedUser,
    headers: &HeaderMap,
) -> AgentLanguageMode {
    let mode_header = headers
        .get(AGENT_LANGUAGE_MODE_HEADER)
        .and_then(|value| value.to_str().ok());
    let allowlist = std::env::var(KURA_AGENT_DEVELOPER_RAW_USER_ALLOWLIST_ENV).ok();
    let mode = resolve_agent_language_mode_with_sources(auth, mode_header, allowlist.as_deref());
    if mode == AgentLanguageMode::DeveloperRaw {
        tracing::info!(
            user_id = %auth.user_id,
            mode = "developer_raw",
            "developer raw language mode enabled for write-with-proof response"
        );
    } else if header_requests_developer_raw(mode_header) {
        tracing::warn!(
            user_id = %auth.user_id,
            "developer raw language mode request denied; enforcing user_safe mode"
        );
    }
    mode
}

pub(super) fn model_tier_policy_from_name(tier_name: &str) -> ModelTierPolicy {
    match tier_name {
        "advanced" => ModelTierPolicy {
            registry_version: MODEL_TIER_REGISTRY_VERSION,
            capability_tier: "advanced",
            confidence_floor: 0.70,
            allowed_action_scope: "proactive",
            high_impact_write_policy: "allow",
            repair_auto_apply_cap: "enabled",
        },
        "moderate" => ModelTierPolicy {
            registry_version: MODEL_TIER_REGISTRY_VERSION,
            capability_tier: "moderate",
            confidence_floor: 0.80,
            allowed_action_scope: "moderate",
            high_impact_write_policy: "confirm_first",
            repair_auto_apply_cap: "confirm_only",
        },
        _ => ModelTierPolicy {
            registry_version: MODEL_TIER_REGISTRY_VERSION,
            capability_tier: "strict",
            confidence_floor: 0.90,
            allowed_action_scope: "strict",
            high_impact_write_policy: "confirm_first",
            repair_auto_apply_cap: "confirm_only",
        },
    }
}

pub(super) fn resolve_model_tier_policy_default() -> ModelTierPolicy {
    // All models start at moderate. Auto-tiering adjusts based on observed quality.
    model_tier_policy_from_name("moderate")
}

pub(super) fn candidate_auto_model_tier(sample_count: i64, mismatch_rate_pct: f64) -> &'static str {
    if sample_count < MODEL_TIER_AUTO_MIN_SAMPLES {
        return "moderate";
    }
    if mismatch_rate_pct <= MODEL_TIER_AUTO_ADVANCED_MAX_MISMATCH_PCT {
        return "advanced";
    }
    if mismatch_rate_pct <= MODEL_TIER_AUTO_MODERATE_MAX_MISMATCH_PCT {
        return "moderate";
    }
    "strict"
}

pub(super) fn apply_model_tier_hysteresis(
    previous_tier: Option<&str>,
    candidate_tier: &str,
    sample_count: i64,
    mismatch_rate_pct: f64,
) -> String {
    let Some(previous) = previous_tier else {
        return candidate_tier.to_string();
    };

    match previous {
        "advanced" => {
            if candidate_tier != "advanced"
                && (sample_count < MODEL_TIER_AUTO_MIN_SAMPLES
                    || mismatch_rate_pct < MODEL_TIER_AUTO_ADVANCED_DEMOTE_PCT)
            {
                return "advanced".to_string();
            }
        }
        "moderate" => {
            if candidate_tier == "advanced"
                && (sample_count < (MODEL_TIER_AUTO_MIN_SAMPLES + 5)
                    || mismatch_rate_pct > MODEL_TIER_AUTO_ADVANCED_PROMOTE_PCT)
            {
                return "moderate".to_string();
            }
            if candidate_tier == "strict" && mismatch_rate_pct < MODEL_TIER_AUTO_STRICT_ENTER_PCT {
                return "moderate".to_string();
            }
        }
        "strict" => {
            if candidate_tier != "strict"
                && (sample_count < (MODEL_TIER_AUTO_MIN_SAMPLES + 3)
                    || mismatch_rate_pct > MODEL_TIER_AUTO_STRICT_EXIT_PCT)
            {
                return "strict".to_string();
            }
        }
        _ => {}
    }

    candidate_tier.to_string()
}

pub(super) async fn resolve_auto_tier_policy(
    state: &AppState,
    user_id: Uuid,
    model_identity: &str,
) -> Result<(ModelTierPolicy, Vec<String>), AppError> {
    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let metrics = sqlx::query_as::<_, ModelTierTelemetryRow>(
        r#"
        SELECT
            COUNT(*)::BIGINT AS sample_count,
            COALESCE(
                SUM(
                    CASE
                        -- Exclude infrastructure-level uncertainty from mismatch accounting.
                        WHEN COALESCE(data->'uncertainty_markers', '[]'::jsonb) ? 'write_receipt_incomplete'
                             OR COALESCE(data->'uncertainty_markers', '[]'::jsonb) ? 'read_after_write_unverified'
                            THEN 0.0
                        -- Severity-aware path: use explicit mismatch_weight when present.
                        WHEN (data->>'mismatch_weight') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                            THEN GREATEST(
                                0.0,
                                LEAST(1.0, (data->>'mismatch_weight')::DOUBLE PRECISION)
                            )
                        -- Legacy fallback: binary mismatch_detected -> weight 1.0 / 0.0.
                        WHEN LOWER(COALESCE(data->>'mismatch_detected', 'false')) = 'true'
                            THEN 1.0
                        ELSE 0.0
                    END
                ),
                0.0
            )::DOUBLE PRECISION AS mismatch_weighted_sum
        FROM events
        WHERE user_id = $1
          AND event_type = 'quality.save_claim.checked'
          AND timestamp >= NOW() - (($3)::TEXT || ' days')::INTERVAL
          AND LOWER(COALESCE(data->'autonomy_gate'->>'effective_quality_status', 'healthy')) <> 'degraded'
          AND COALESCE(
                NULLIF(data->>'runtime_model_identity', ''),
                NULLIF(data->'autonomy_policy'->>'model_identity', '')
          ) = $2
        "#,
    )
    .bind(user_id)
    .bind(model_identity)
    .bind(MODEL_TIER_AUTO_LOOKBACK_DAYS)
    .fetch_one(&mut *tx)
    .await?;

    let previous_tier = sqlx::query_scalar::<_, Option<String>>(
        r#"
        SELECT data->'autonomy_policy'->>'capability_tier'
        FROM events
        WHERE user_id = $1
          AND event_type = 'quality.save_claim.checked'
          AND timestamp >= NOW() - (($3)::TEXT || ' days')::INTERVAL
          AND LOWER(COALESCE(data->'autonomy_gate'->>'effective_quality_status', 'healthy')) <> 'degraded'
          AND COALESCE(
                NULLIF(data->>'runtime_model_identity', ''),
                NULLIF(data->'autonomy_policy'->>'model_identity', '')
          ) = $2
        ORDER BY timestamp DESC
        LIMIT 1
        "#,
    )
    .bind(user_id)
    .bind(model_identity)
    .bind(MODEL_TIER_AUTO_LOOKBACK_DAYS)
    .fetch_optional(&mut *tx)
    .await?
    .flatten();

    tx.commit().await?;

    let sample_count = metrics.sample_count.max(0);
    let mismatch_weighted_sum = if metrics.mismatch_weighted_sum.is_finite() {
        metrics.mismatch_weighted_sum.max(0.0)
    } else {
        0.0
    };
    let mismatch_rate_pct = if sample_count > 0 {
        (mismatch_weighted_sum / sample_count as f64) * 100.0
    } else {
        0.0
    };

    let candidate = candidate_auto_model_tier(sample_count, mismatch_rate_pct);
    let effective_tier = apply_model_tier_hysteresis(
        previous_tier.as_deref(),
        candidate,
        sample_count,
        mismatch_rate_pct,
    );

    let mut reason_codes = Vec::new();
    if sample_count < MODEL_TIER_AUTO_MIN_SAMPLES {
        reason_codes.push(MODEL_TIER_AUTO_LOW_SAMPLES_CONFIRM_REASON_CODE.to_string());
    }
    if effective_tier == "strict" && sample_count >= MODEL_TIER_AUTO_MIN_SAMPLES {
        reason_codes.push(MODEL_TIER_AUTO_STRICT_REASON_CODE.to_string());
    }
    dedupe_reason_codes(&mut reason_codes);

    Ok((model_tier_policy_from_name(&effective_tier), reason_codes))
}

pub(super) async fn resolve_model_tier_policy_for_write(
    state: &AppState,
    user_id: Uuid,
    resolved_model_identity: &ResolvedModelIdentity,
) -> Result<(ModelTierPolicy, Vec<String>), AppError> {
    // Auto-tiering for all models, regardless of attestation source.
    // Model identity is used as quality track key (audit), not for tier assignment.
    resolve_auto_tier_policy(state, user_id, &resolved_model_identity.model_identity).await
}

pub(super) fn dedupe_reason_codes(reason_codes: &mut Vec<String>) {
    let mut seen = HashSet::new();
    reason_codes.retain(|code| seen.insert(code.clone()));
}

pub(super) fn build_agent_self_model(
    model_identity: &ResolvedModelIdentity,
    tier_policy: &ModelTierPolicy,
) -> AgentSelfModel {
    let mut known_limitations = match tier_policy.capability_tier {
        "strict" => vec![
            "High-impact writes require confirm-first + mandatory intent_handshake in strict tier."
                .to_string(),
            "Repair auto-apply is confirmation-gated in strict tier.".to_string(),
            "Tier was reduced by auto-tiering due to observed quality issues.".to_string(),
        ],
        "moderate" => vec![
            "High-impact writes require confirm-first in moderate tier.".to_string(),
            "Repair auto-apply remains confirmation-gated in moderate tier.".to_string(),
            "All models start at moderate; advancement to trusted requires consistent quality."
                .to_string(),
        ],
        _ => vec![
            "Autonomy can still be reduced by calibration or integrity regressions.".to_string(),
        ],
    };
    if model_identity
        .reason_codes
        .iter()
        .any(|code| code == MODEL_IDENTITY_UNKNOWN_FALLBACK_REASON_CODE)
    {
        known_limitations.push(
            "Model identity could not be resolved; used as audit label only, does not affect tier."
                .to_string(),
        );
    }

    AgentSelfModel {
        schema_version: AGENT_SELF_MODEL_SCHEMA_VERSION.to_string(),
        model_identity: model_identity.model_identity.clone(),
        capability_tier: tier_policy.capability_tier.to_string(),
        known_limitations,
        preferred_contracts: AgentSelfModelPreferredContracts {
            read: "/v1/agent/context".to_string(),
            write: "/v1/agent/write-with-proof".to_string(),
        },
        fallback_behavior: AgentSelfModelFallbackBehavior {
            unknown_identity_action: "fallback_moderate".to_string(),
            unknown_policy_action: "auto_tier".to_string(),
        },
        docs: AgentSelfModelDocs {
            runtime_policy: "system.conventions.model_tier_registry_v1".to_string(),
            upgrade_hint: "/v1/agent/capabilities".to_string(),
        },
    }
}

pub(super) fn build_agent_capabilities_with_self_model(
    self_model: AgentSelfModel,
) -> AgentCapabilitiesResponse {
    AgentCapabilitiesResponse {
        schema_version: AGENT_CAPABILITIES_SCHEMA_VERSION.to_string(),
        protocol_version: "2026-02-11.agent-contract.v1".to_string(),
        preferred_read_endpoint: "/v1/agent/context".to_string(),
        preferred_write_endpoint: "/v1/agent/write-with-proof".to_string(),
        self_model,
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

pub(super) fn build_agent_capabilities() -> AgentCapabilitiesResponse {
    let model_identity = resolve_model_identity_with_sources(None, None, None);
    let tier_policy = resolve_model_tier_policy_default();
    let self_model = build_agent_self_model(&model_identity, &tier_policy);
    build_agent_capabilities_with_self_model(self_model)
}
