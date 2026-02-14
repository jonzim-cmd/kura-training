use super::*;

pub(super) fn all_read_after_write_verified(checks: &[AgentReadAfterWriteCheck]) -> bool {
    checks.iter().all(|check| check.status == "verified")
}

pub(super) fn scope_rank(scope_level: &str) -> u8 {
    match scope_level.trim().to_lowercase().as_str() {
        "strict" => 0,
        "moderate" => 1,
        "proactive" => 2,
        _ => 0,
    }
}

pub(super) fn stricter_scope_level(current_scope: &str, tier_scope: &str) -> String {
    if scope_rank(current_scope) <= scope_rank(tier_scope) {
        current_scope.trim().to_lowercase()
    } else {
        tier_scope.trim().to_lowercase()
    }
}

pub(super) fn normalize_quality_status(raw_status: &str) -> &'static str {
    match raw_status.trim().to_lowercase().as_str() {
        "degraded" => "degraded",
        "monitor" => "monitor",
        _ => "healthy",
    }
}

pub(super) fn normalize_autonomy_scope_override(raw: Option<&str>) -> Option<String> {
    let value = raw?.trim().to_lowercase();
    match value.as_str() {
        "strict" | "moderate" | "proactive" => Some(value),
        _ => None,
    }
}

pub(super) fn normalize_verbosity_override(raw: Option<&str>) -> Option<String> {
    let value = raw?.trim().to_lowercase();
    match value.as_str() {
        "concise" | "short" | "brief" => Some("concise".to_string()),
        "balanced" | "normal" | "default" => Some("balanced".to_string()),
        "detailed" | "verbose" | "long" => Some("detailed".to_string()),
        _ => None,
    }
}

pub(super) fn normalize_confirmation_strictness_override(raw: Option<&str>) -> Option<String> {
    let value = raw?.trim().to_lowercase();
    match value.as_str() {
        "auto" => Some("auto".to_string()),
        "always" | "strict" => Some("always".to_string()),
        "never" | "relaxed" => Some("never".to_string()),
        _ => None,
    }
}

pub(super) fn policy_requires_confirmation(autonomy_policy: &AgentAutonomyPolicy) -> bool {
    autonomy_policy.throttle_active
        || autonomy_policy.require_confirmation_for_non_trivial_actions
        || autonomy_policy.require_confirmation_for_plan_updates
        || autonomy_policy.require_confirmation_for_repairs
}

fn phrase_by_verbosity(verbosity: &str, concise: &str, balanced: &str, detailed: &str) -> String {
    match verbosity.trim().to_lowercase().as_str() {
        "concise" => concise.to_string(),
        "detailed" => detailed.to_string(),
        _ => balanced.to_string(),
    }
}

pub(super) fn apply_user_preference_overrides(
    mut autonomy_policy: AgentAutonomyPolicy,
    user_profile: Option<&ProjectionResponse>,
) -> AgentAutonomyPolicy {
    let scope_raw = user_preference_string(user_profile, "autonomy_scope");
    let verbosity_raw = user_preference_string(user_profile, "verbosity");
    let confirmation_raw = user_preference_string(user_profile, "confirmation_strictness");

    if let Some(verbosity) = normalize_verbosity_override(verbosity_raw.as_deref()) {
        autonomy_policy.interaction_verbosity = verbosity;
    }

    if let Some(scope_level) = normalize_autonomy_scope_override(scope_raw.as_deref()) {
        let current_scope = autonomy_policy.max_scope_level.clone();
        let healthy_quality = normalize_quality_status(&autonomy_policy.slo_status) == "healthy"
            && normalize_quality_status(&autonomy_policy.calibration_status) == "healthy"
            && !autonomy_policy.throttle_active;
        autonomy_policy.user_requested_scope_level = Some(scope_level.clone());
        autonomy_policy.max_scope_level = if healthy_quality {
            scope_level
        } else {
            stricter_scope_level(&scope_level, &current_scope)
        };
    }

    if let Some(confirmation_mode) =
        normalize_confirmation_strictness_override(confirmation_raw.as_deref())
    {
        autonomy_policy.confirmation_strictness = confirmation_mode.clone();
        if confirmation_mode == "always" {
            autonomy_policy.require_confirmation_for_non_trivial_actions = true;
            autonomy_policy.require_confirmation_for_plan_updates = true;
            autonomy_policy.require_confirmation_for_repairs = true;
            autonomy_policy.repair_auto_apply_enabled = false;
        } else if confirmation_mode == "never" {
            let relaxed_mode_allowed = normalize_quality_status(&autonomy_policy.slo_status)
                == "healthy"
                && normalize_quality_status(&autonomy_policy.calibration_status) == "healthy"
                && !autonomy_policy.throttle_active;
            if relaxed_mode_allowed {
                autonomy_policy.require_confirmation_for_non_trivial_actions = false;
                autonomy_policy.require_confirmation_for_plan_updates = false;
                autonomy_policy.require_confirmation_for_repairs = false;
                autonomy_policy.repair_auto_apply_enabled = true;
            }
        }
    }

    autonomy_policy
}

pub(super) fn worst_quality_status(left: &str, right: &str) -> &'static str {
    let left_rank = match normalize_quality_status(left) {
        "degraded" => 2,
        "monitor" => 1,
        _ => 0,
    };
    let right_rank = match normalize_quality_status(right) {
        "degraded" => 2,
        "monitor" => 1,
        _ => 0,
    };

    if left_rank >= right_rank {
        normalize_quality_status(left)
    } else {
        normalize_quality_status(right)
    }
}

pub(super) fn classify_write_action_class(events: &[CreateEventRequest]) -> String {
    let high_impact = events.iter().any(|event| {
        let event_type = event.event_type.trim().to_lowercase();
        is_planning_or_coaching_event_type(&event_type)
            || event_type == WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE
            || event_type == WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE
    });

    if high_impact {
        "high_impact_write".to_string()
    } else {
        "low_impact_write".to_string()
    }
}

pub(super) fn summarize_high_impact_change_set(events: &[CreateEventRequest]) -> Vec<String> {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for event in events {
        let event_type = event.event_type.trim().to_lowercase();
        if is_planning_or_coaching_event_type(&event_type)
            || event_type == WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE
            || event_type == WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE
        {
            *counts.entry(event_type).or_insert(0) += 1;
        }
    }
    counts
        .into_iter()
        .map(|(event_type, count)| format!("{event_type}:{count}"))
        .collect()
}

pub(super) fn validate_high_impact_confirmation(
    confirmation: Option<&AgentHighImpactConfirmation>,
    events: &[CreateEventRequest],
    autonomy_gate: &AgentAutonomyGate,
    user_id: Uuid,
    action_class: &str,
    request_digest: &str,
    secret: Option<&str>,
    now: DateTime<Utc>,
) -> Result<(), AppError> {
    let mut reason_codes = autonomy_gate.reason_codes.clone();
    reason_codes.push(HIGH_IMPACT_CONFIRMATION_REQUIRED_REASON_CODE.to_string());
    dedupe_reason_codes(&mut reason_codes);

    let Some(secret_value) = secret.and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed)
        }
    }) else {
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_SECRET_UNCONFIGURED_REASON_CODE.to_string());
        dedupe_reason_codes(&mut reason_codes);
        return Err(AppError::Validation {
            message: "High-impact confirmation secret is not configured.".to_string(),
            field: Some("high_impact_confirmation.confirmation_token".to_string()),
            received: Some(json!({
                "reason_codes": reason_codes,
            })),
            docs_hint: Some(
                "Set KURA_AGENT_MODEL_ATTESTATION_SECRET so confirmation tokens can be issued and verified."
                    .to_string(),
            ),
        });
    };

    let mut confirmation_reasons = autonomy_gate.reason_codes.clone();
    confirmation_reasons.push(HIGH_IMPACT_CONFIRMATION_REQUIRED_REASON_CODE.to_string());
    dedupe_reason_codes(&mut confirmation_reasons);

    let pending_change_set = {
        let summary = summarize_high_impact_change_set(events);
        if summary.is_empty() {
            vec!["high_impact_write:1".to_string()]
        } else {
            summary
        }
    };

    let docs_hint = format!(
        "Show pending_change_set to the user, then resend with high_impact_confirmation {{ schema_version: '{HIGH_IMPACT_CONFIRMATION_SCHEMA_VERSION}', confirmed: true, confirmed_at: <current_utc_timestamp>, confirmation_token: <confirmation_token> }}."
    );
    let Some(confirmation) = confirmation else {
        let token = issue_high_impact_confirmation_token(
            secret_value,
            user_id,
            action_class,
            request_digest,
            now,
        );
        return Err(AppError::Validation {
            message: "Explicit user confirmation is required for this high-impact write."
                .to_string(),
            field: Some("high_impact_confirmation".to_string()),
            received: Some(json!({
                "required_reason_codes": confirmation_reasons,
                "pending_change_set": pending_change_set,
                "confirmation_token": token,
                "confirmation_token_ttl_minutes": HIGH_IMPACT_CONFIRMATION_MAX_AGE_MINUTES,
            })),
            docs_hint: Some(docs_hint),
        });
    };

    let confirmation_token = confirmation
        .confirmation_token
        .as_deref()
        .map(str::trim)
        .filter(|token| !token.is_empty());
    let Some(confirmation_token) = confirmation_token else {
        let mut reason_codes = confirmation_reasons.clone();
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_TOKEN_MISSING_REASON_CODE.to_string());
        dedupe_reason_codes(&mut reason_codes);
        return Err(AppError::Validation {
            message: "high_impact_confirmation.confirmation_token is required".to_string(),
            field: Some("high_impact_confirmation.confirmation_token".to_string()),
            received: Some(json!({
                "reason_codes": reason_codes,
            })),
            docs_hint: Some(
                "Replay the latest confirm-first request payload with the confirmation_token returned by Kura."
                    .to_string(),
            ),
        });
    };

    if let Err(mut token_reason_codes) = verify_high_impact_confirmation_token(
        confirmation_token,
        secret_value,
        user_id,
        action_class,
        request_digest,
        now,
    ) {
        token_reason_codes.push(HIGH_IMPACT_CONFIRMATION_INVALID_REASON_CODE.to_string());
        dedupe_reason_codes(&mut token_reason_codes);
        return Err(AppError::Validation {
            message: "high_impact_confirmation.confirmation_token is invalid".to_string(),
            field: Some("high_impact_confirmation.confirmation_token".to_string()),
            received: Some(json!({
                "reason_codes": token_reason_codes,
                "pending_change_set": pending_change_set,
            })),
            docs_hint: Some(
                "Request a fresh confirm-first challenge and resend the unchanged write payload with the new token."
                    .to_string(),
            ),
        });
    }

    if confirmation.schema_version.trim() != HIGH_IMPACT_CONFIRMATION_SCHEMA_VERSION {
        let mut reason_codes = confirmation_reasons.clone();
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_INVALID_REASON_CODE.to_string());
        dedupe_reason_codes(&mut reason_codes);
        return Err(AppError::Validation {
            message: "high_impact_confirmation.schema_version is not supported".to_string(),
            field: Some("high_impact_confirmation.schema_version".to_string()),
            received: Some(json!({
                "schema_version": confirmation.schema_version,
                "reason_codes": reason_codes,
            })),
            docs_hint: Some(format!(
                "Use schema_version '{HIGH_IMPACT_CONFIRMATION_SCHEMA_VERSION}'."
            )),
        });
    }
    if !confirmation.confirmed {
        let mut reason_codes = confirmation_reasons.clone();
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_INVALID_REASON_CODE.to_string());
        dedupe_reason_codes(&mut reason_codes);
        return Err(AppError::Validation {
            message: "high_impact_confirmation.confirmed must be true".to_string(),
            field: Some("high_impact_confirmation.confirmed".to_string()),
            received: Some(json!({
                "confirmed": confirmation.confirmed,
                "reason_codes": reason_codes,
            })),
            docs_hint: Some(
                "Set confirmed=true only after the user explicitly approves the pending change set."
                    .to_string(),
            ),
        });
    }

    let age = now.signed_duration_since(confirmation.confirmed_at);
    if age > chrono::Duration::minutes(HIGH_IMPACT_CONFIRMATION_MAX_AGE_MINUTES)
        || age < chrono::Duration::minutes(-HIGH_IMPACT_CONFIRMATION_MAX_FUTURE_SKEW_MINUTES)
    {
        let mut reason_codes = confirmation_reasons.clone();
        reason_codes.push(HIGH_IMPACT_CONFIRMATION_INVALID_REASON_CODE.to_string());
        dedupe_reason_codes(&mut reason_codes);
        return Err(AppError::Validation {
            message: "high_impact_confirmation is stale".to_string(),
            field: Some("high_impact_confirmation.confirmed_at".to_string()),
            received: Some(json!({
                "confirmed_at": confirmation.confirmed_at,
                "reason_codes": reason_codes,
            })),
            docs_hint: Some(format!(
                "Send confirmation within {HIGH_IMPACT_CONFIRMATION_MAX_AGE_MINUTES} minutes of execution."
            )),
        });
    }

    Ok(())
}

pub(super) fn validate_intent_handshake(
    handshake: &AgentIntentHandshake,
    action_class: &str,
) -> Result<(), AppError> {
    if handshake.schema_version.trim() != INTENT_HANDSHAKE_SCHEMA_VERSION {
        return Err(AppError::Validation {
            message: "intent_handshake.schema_version is not supported".to_string(),
            field: Some("intent_handshake.schema_version".to_string()),
            received: Some(json!(handshake.schema_version)),
            docs_hint: Some(format!(
                "Use schema_version '{INTENT_HANDSHAKE_SCHEMA_VERSION}'."
            )),
        });
    }

    if handshake.goal.trim().is_empty() {
        return Err(AppError::Validation {
            message: "intent_handshake.goal must not be empty".to_string(),
            field: Some("intent_handshake.goal".to_string()),
            received: Some(json!(handshake.goal)),
            docs_hint: Some("Provide a concise execution goal.".to_string()),
        });
    }
    if handshake.planned_action.trim().is_empty() {
        return Err(AppError::Validation {
            message: "intent_handshake.planned_action must not be empty".to_string(),
            field: Some("intent_handshake.planned_action".to_string()),
            received: Some(json!(handshake.planned_action)),
            docs_hint: Some("Describe the planned write action before execution.".to_string()),
        });
    }
    if handshake.success_criteria.trim().is_empty() {
        return Err(AppError::Validation {
            message: "intent_handshake.success_criteria must not be empty".to_string(),
            field: Some("intent_handshake.success_criteria".to_string()),
            received: Some(json!(handshake.success_criteria)),
            docs_hint: Some("Define how success is validated.".to_string()),
        });
    }
    if handshake.assumptions.is_empty() {
        return Err(AppError::Validation {
            message: "intent_handshake.assumptions must not be empty".to_string(),
            field: Some("intent_handshake.assumptions".to_string()),
            received: None,
            docs_hint: Some("List at least one explicit assumption.".to_string()),
        });
    }
    if handshake.non_goals.is_empty() {
        return Err(AppError::Validation {
            message: "intent_handshake.non_goals must not be empty".to_string(),
            field: Some("intent_handshake.non_goals".to_string()),
            received: None,
            docs_hint: Some("List at least one explicit non-goal.".to_string()),
        });
    }

    let impact_class = handshake.impact_class.trim().to_lowercase();
    if impact_class != "high_impact_write" && impact_class != "low_impact_write" {
        return Err(AppError::Validation {
            message: "intent_handshake.impact_class must be low_impact_write or high_impact_write"
                .to_string(),
            field: Some("intent_handshake.impact_class".to_string()),
            received: Some(json!(handshake.impact_class)),
            docs_hint: Some("Set impact_class to match the intended write scope.".to_string()),
        });
    }
    if impact_class != action_class {
        return Err(AppError::Validation {
            message: "intent_handshake.impact_class does not match detected action class"
                .to_string(),
            field: Some("intent_handshake.impact_class".to_string()),
            received: Some(json!({
                "handshake": impact_class,
                "detected_action_class": action_class,
            })),
            docs_hint: Some(
                "Refresh the handshake for the current action scope before executing.".to_string(),
            ),
        });
    }

    let max_age = chrono::Duration::minutes(INTENT_HANDSHAKE_MAX_AGE_MINUTES);
    if Utc::now() - handshake.created_at > max_age {
        return Err(AppError::Validation {
            message: "intent_handshake is stale".to_string(),
            field: Some("intent_handshake.created_at".to_string()),
            received: Some(json!(handshake.created_at)),
            docs_hint: Some(format!(
                "Create a fresh handshake within {INTENT_HANDSHAKE_MAX_AGE_MINUTES} minutes of execution."
            )),
        });
    }

    Ok(())
}

pub(super) fn build_intent_handshake_confirmation(
    handshake: &AgentIntentHandshake,
) -> AgentIntentHandshakeConfirmation {
    AgentIntentHandshakeConfirmation {
        schema_version: INTENT_HANDSHAKE_SCHEMA_VERSION.to_string(),
        status: "accepted".to_string(),
        impact_class: handshake.impact_class.trim().to_lowercase(),
        handshake_id: handshake.handshake_id.clone(),
        chat_confirmation: format!(
            "Intent bestätigt: Ziel='{}', Aktion='{}', Erfolg='{}'.",
            handshake.goal.trim(),
            handshake.planned_action.trim(),
            handshake.success_criteria.trim(),
        ),
    }
}

pub(super) fn merge_autonomy_gate_with_memory_guard(
    mut gate: AgentAutonomyGate,
    action_class: &str,
    user_profile: Option<&ProjectionResponse>,
) -> AgentAutonomyGate {
    let Some(reason_code) = memory_tier_confirm_reason(action_class, user_profile, Utc::now())
    else {
        return gate;
    };

    if gate.decision == "allow" {
        gate.decision = "confirm_first".to_string();
    }
    gate.reason_codes.push(reason_code);
    dedupe_reason_codes(&mut gate.reason_codes);
    gate
}

pub(super) fn apply_model_tier_policy(
    mut autonomy_policy: AgentAutonomyPolicy,
    model_identity: &str,
    tier_policy: &ModelTierPolicy,
    model_identity_reason_codes: &[String],
) -> AgentAutonomyPolicy {
    autonomy_policy.model_identity = model_identity.to_string();
    autonomy_policy.capability_tier = tier_policy.capability_tier.to_string();
    autonomy_policy.tier_policy_version = tier_policy.registry_version.to_string();
    autonomy_policy.tier_confidence_floor = tier_policy.confidence_floor;
    autonomy_policy.max_scope_level = stricter_scope_level(
        &autonomy_policy.max_scope_level,
        tier_policy.allowed_action_scope,
    );

    match tier_policy.repair_auto_apply_cap {
        "disabled" | "confirm_only" => {
            autonomy_policy.repair_auto_apply_enabled = false;
            autonomy_policy.require_confirmation_for_repairs = true;
        }
        _ => {}
    }

    if !model_identity_reason_codes.is_empty() {
        autonomy_policy.reason = format!(
            "{} [model_identity_resolution={}]",
            autonomy_policy.reason,
            model_identity_reason_codes.join(",")
        );
    }

    autonomy_policy
}

pub(super) fn evaluate_autonomy_gate(
    action_class: &str,
    autonomy_policy: &AgentAutonomyPolicy,
    tier_policy: &ModelTierPolicy,
    base_reason_codes: &[String],
) -> AgentAutonomyGate {
    let mut reason_codes = base_reason_codes.to_vec();
    let effective_quality_status = worst_quality_status(
        &autonomy_policy.slo_status,
        &autonomy_policy.calibration_status,
    );
    let mut decision = "allow".to_string();

    if action_class == "high_impact_write" {
        if effective_quality_status == "degraded" {
            decision = "confirm_first".to_string();
            if normalize_quality_status(&autonomy_policy.calibration_status) == "degraded" {
                reason_codes.push(CALIBRATION_DEGRADED_CONFIRM_REASON_CODE.to_string());
            }
            if normalize_quality_status(&autonomy_policy.slo_status) == "degraded" {
                reason_codes.push(INTEGRITY_DEGRADED_CONFIRM_REASON_CODE.to_string());
            }
            if reason_codes.is_empty() {
                reason_codes.push(INTEGRITY_DEGRADED_CONFIRM_REASON_CODE.to_string());
            }
        } else if effective_quality_status == "monitor" {
            decision = "confirm_first".to_string();
            if normalize_quality_status(&autonomy_policy.calibration_status) == "monitor" {
                reason_codes.push(CALIBRATION_MONITOR_CONFIRM_REASON_CODE.to_string());
            } else {
                reason_codes.push(INTEGRITY_MONITOR_CONFIRM_REASON_CODE.to_string());
            }
        } else if autonomy_policy.require_confirmation_for_non_trivial_actions {
            decision = "confirm_first".to_string();
            reason_codes.push(USER_CONFIRMATION_STRICTNESS_ALWAYS_REASON_CODE.to_string());
        } else if tier_policy.high_impact_write_policy == "confirm_first" {
            decision = "confirm_first".to_string();
            if tier_policy.capability_tier == "strict" {
                reason_codes.push(MODEL_TIER_STRICT_CONFIRM_REASON_CODE.to_string());
            } else {
                reason_codes.push(MODEL_TIER_CONFIRM_REASON_CODE.to_string());
            }
        }
    }

    dedupe_reason_codes(&mut reason_codes);

    AgentAutonomyGate {
        decision,
        action_class: action_class.to_string(),
        model_tier: tier_policy.capability_tier.to_string(),
        effective_quality_status: effective_quality_status.to_string(),
        reason_codes,
    }
}

pub(super) fn default_autonomy_gate() -> AgentAutonomyGate {
    AgentAutonomyGate {
        decision: "allow".to_string(),
        action_class: "low_impact_write".to_string(),
        model_tier: "moderate".to_string(),
        effective_quality_status: "healthy".to_string(),
        reason_codes: Vec::new(),
    }
}

pub(super) fn default_autonomy_policy() -> AgentAutonomyPolicy {
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
        model_identity: "unknown".to_string(),
        capability_tier: "strict".to_string(),
        tier_policy_version: MODEL_TIER_REGISTRY_VERSION.to_string(),
        tier_confidence_floor: 0.90,
        throttle_active: false,
        max_scope_level: "moderate".to_string(),
        interaction_verbosity: "balanced".to_string(),
        confirmation_strictness: "auto".to_string(),
        user_requested_scope_level: None,
        require_confirmation_for_non_trivial_actions: false,
        require_confirmation_for_plan_updates: false,
        require_confirmation_for_repairs: false,
        repair_auto_apply_enabled: true,
        reason: "No quality_health autonomy policy available; using healthy defaults.".to_string(),
        confirmation_templates: templates,
    }
}

pub(super) fn parse_confirmation_templates(
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

pub(super) fn autonomy_policy_from_quality_health(
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
        model_identity: policy
            .get("model_identity")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string(),
        capability_tier: policy
            .get("capability_tier")
            .and_then(Value::as_str)
            .unwrap_or("strict")
            .to_string(),
        tier_policy_version: policy
            .get("tier_policy_version")
            .and_then(Value::as_str)
            .unwrap_or(MODEL_TIER_REGISTRY_VERSION)
            .to_string(),
        tier_confidence_floor: policy
            .get("tier_confidence_floor")
            .and_then(Value::as_f64)
            .unwrap_or(0.90),
        throttle_active: policy
            .get("throttle_active")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        max_scope_level: policy
            .get("max_scope_level")
            .and_then(Value::as_str)
            .unwrap_or("moderate")
            .to_string(),
        interaction_verbosity: policy
            .get("interaction_verbosity")
            .and_then(Value::as_str)
            .unwrap_or("balanced")
            .to_string(),
        confirmation_strictness: policy
            .get("confirmation_strictness")
            .and_then(Value::as_str)
            .unwrap_or("auto")
            .to_string(),
        user_requested_scope_level: policy
            .get("user_requested_scope_level")
            .and_then(Value::as_str)
            .map(|value| value.to_string()),
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

pub(super) fn build_claim_guard(
    receipts: &[AgentWriteReceipt],
    requested_event_count: usize,
    checks: &[AgentReadAfterWriteCheck],
    warnings: &[BatchEventWarning],
    autonomy_policy: AgentAutonomyPolicy,
    autonomy_gate: AgentAutonomyGate,
) -> AgentWriteClaimGuard {
    let mut uncertainty_markers = Vec::new();
    let mut deferred_markers = Vec::new();
    let requires_confirmation =
        policy_requires_confirmation(&autonomy_policy) || autonomy_gate.decision == "confirm_first";

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

    if requires_confirmation {
        uncertainty_markers.push("autonomy_throttled_by_integrity_slo".to_string());
        deferred_markers.push("confirm_non_trivial_actions_due_to_slo_regression".to_string());
    }
    if autonomy_gate.decision == "confirm_first" {
        uncertainty_markers.push("autonomy_confirm_first_by_model_tier".to_string());
        deferred_markers.push("confirm_high_impact_action_due_to_model_tier".to_string());
    }

    let next_action_confirmation_prompt = if requires_confirmation {
        autonomy_policy
            .confirmation_templates
            .get("non_trivial_action")
            .cloned()
    } else {
        None
    };

    let allow_saved_claim = receipts_complete && read_after_write_ok;
    let (claim_status, recommended_user_phrase) = if allow_saved_claim && requires_confirmation {
        (
            "saved_verified".to_string(),
            autonomy_policy
                .confirmation_templates
                .get("post_save_followup")
                .cloned()
                .unwrap_or_else(|| {
                    phrase_by_verbosity(
                        &autonomy_policy.interaction_verbosity,
                        "Saved. Nächste nicht-triviale Schritte nur mit Bestätigung.",
                        &format!(
                            "Saved and verified in the read model. Integrity/model status requires explicit confirmation before non-trivial follow-up actions (tier='{}', quality='{}').",
                            autonomy_gate.model_tier,
                            autonomy_gate.effective_quality_status,
                        ),
                        &format!(
                            "Saved and verified (durable receipt + read-after-write). Because current integrity/model guardrails are active (tier='{}', quality='{}'), non-trivial follow-up actions require explicit user confirmation.",
                            autonomy_gate.model_tier,
                            autonomy_gate.effective_quality_status,
                        ),
                    )
                }),
        )
    } else if allow_saved_claim {
        (
            "saved_verified".to_string(),
            phrase_by_verbosity(
                &autonomy_policy.interaction_verbosity,
                "Saved.",
                "Saved and verified in the read model.",
                "Saved and verified in the read model (durable receipt + read-after-write check).",
            ),
        )
    } else if !receipts_complete {
        (
            "failed".to_string(),
            phrase_by_verbosity(
                &autonomy_policy.interaction_verbosity,
                "Saved claim failed: missing durable receipts.",
                "Write proof incomplete (missing durable receipts). Avoid a saved claim and retry with the same idempotency keys.",
                "Write proof is incomplete because durable receipts are missing. Do not claim 'saved'; retry using the same idempotency keys so the write remains idempotent.",
            ),
        )
    } else {
        (
            "pending".to_string(),
            phrase_by_verbosity(
                &autonomy_policy.interaction_verbosity,
                "Saved claim pending verification.",
                "Write accepted; verification still pending, so avoid a definitive 'saved' claim.",
                "Write was accepted, but read-after-write verification is still pending. Avoid any definitive 'saved' claim until projection readback is verified.",
            ),
        )
    };

    AgentWriteClaimGuard {
        allow_saved_claim,
        claim_status,
        uncertainty_markers,
        deferred_markers,
        recommended_user_phrase,
        next_action_confirmation_prompt,
        autonomy_gate,
        autonomy_policy,
    }
}

pub(super) fn build_save_claim_checked_event(
    requested_event_count: usize,
    receipts: &[AgentWriteReceipt],
    verification: &AgentWriteVerificationSummary,
    claim_guard: &AgentWriteClaimGuard,
    session_audit: &AgentSessionAuditSummary,
    model_identity: &ResolvedModelIdentity,
) -> CreateEventRequest {
    let mismatch_detected = !claim_guard.allow_saved_claim;
    // Save-Echo is a tier-independent data-integrity contract (save_echo_policy_v1).
    // It is always required when claim_status indicates persisted data.
    // Completeness defaults to "not_assessed" at write time — the caller (agent
    // response layer) may later upgrade to "partial"/"complete"/"missing" once
    // user-facing echo content is actually evaluated.
    let save_echo_required = matches!(
        claim_guard.claim_status.as_str(),
        "saved_verified" | "inferred"
    );
    let save_echo_completeness = if save_echo_required {
        "not_assessed"
    } else {
        "not_applicable"
    };
    let (severity, mismatch_reason_codes) = classify_mismatch_severity(
        mismatch_detected,
        save_echo_required,
        save_echo_completeness,
    );
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
        "mismatch_severity": severity.severity,
        "mismatch_weight": severity.weight,
        "mismatch_domain": severity.domain,
        "mismatch_reason_codes": mismatch_reason_codes,
        "save_echo_required": save_echo_required,
        "save_echo_present": serde_json::Value::Null,
        "save_echo_completeness": save_echo_completeness,
        "runtime_model_identity": model_identity.model_identity,
        "model_identity_source": model_identity.source,
        "model_attestation_request_id": model_identity.attestation_request_id,
        "next_action_confirmation_prompt": claim_guard.next_action_confirmation_prompt,
        "uncertainty_markers": claim_guard.uncertainty_markers,
        "deferred_markers": claim_guard.deferred_markers,
        "autonomy_policy": {
            "slo_status": claim_guard.autonomy_policy.slo_status,
            "calibration_status": claim_guard.autonomy_policy.calibration_status,
            "model_identity": claim_guard.autonomy_policy.model_identity,
            "capability_tier": claim_guard.autonomy_policy.capability_tier,
            "throttle_active": claim_guard.autonomy_policy.throttle_active,
            "max_scope_level": claim_guard.autonomy_policy.max_scope_level,
            "interaction_verbosity": claim_guard.autonomy_policy.interaction_verbosity,
            "confirmation_strictness": claim_guard.autonomy_policy.confirmation_strictness,
            "user_requested_scope_level": claim_guard.autonomy_policy.user_requested_scope_level,
        },
        "autonomy_gate": {
            "decision": claim_guard.autonomy_gate.decision,
            "action_class": claim_guard.autonomy_gate.action_class,
            "model_tier": claim_guard.autonomy_gate.model_tier,
            "effective_quality_status": claim_guard.autonomy_gate.effective_quality_status,
            "reason_codes": claim_guard.autonomy_gate.reason_codes,
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

// ── Mismatch Severity Classification (save_claim_mismatch_severity contract) ──
//
// Severity reflects data-integrity risk, not protocol aesthetics:
// - critical: No value echo or proof failure without echo assessment
// - warning:  Partial echo → some values visible, incomplete coverage
// - info:     Protocol detail missing (e.g. event-ID) but values correctly mirrored
// - none:     No mismatch or claim not applicable

#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct MismatchSeverity {
    pub severity: &'static str,
    pub weight: f64,
    pub domain: &'static str,
}

pub(super) const MISMATCH_SEVERITY_CRITICAL: MismatchSeverity = MismatchSeverity {
    severity: "critical",
    weight: 1.0,
    domain: "save_echo",
};

pub(super) const MISMATCH_SEVERITY_WARNING: MismatchSeverity = MismatchSeverity {
    severity: "warning",
    weight: 0.5,
    domain: "save_echo",
};

pub(super) const MISMATCH_SEVERITY_INFO: MismatchSeverity = MismatchSeverity {
    severity: "info",
    weight: 0.1,
    domain: "protocol",
};

pub(super) const MISMATCH_SEVERITY_NONE: MismatchSeverity = MismatchSeverity {
    severity: "none",
    weight: 0.0,
    domain: "none",
};

/// Classify mismatch severity based on save-echo completeness and proof state.
///
/// The classification hierarchy (highest to lowest risk):
/// 1. critical — echo missing entirely on a persisted write (save_echo_completeness == "missing")
/// 2. warning  — echo partial (some values mirrored, not all)
/// 3. info     — echo complete but proof-verification failed (protocol-level, not data-level)
/// 4. none     — no mismatch (echo complete + proof verified) or claim not applicable
pub(super) fn classify_mismatch_severity(
    mismatch_detected: bool,
    save_echo_required: bool,
    save_echo_completeness: &str,
) -> (MismatchSeverity, Vec<String>) {
    if !mismatch_detected && !save_echo_required {
        return (MISMATCH_SEVERITY_NONE, vec![]);
    }

    if !mismatch_detected && save_echo_completeness == "complete" {
        return (MISMATCH_SEVERITY_NONE, vec![]);
    }

    let mut reason_codes = Vec::new();

    // No save-echo contract applies (e.g. claim pending/failed). Mismatch here is
    // protocol friction, not a data-integrity break.
    if mismatch_detected && !save_echo_required {
        reason_codes.push("proof_verification_pending_without_save_echo_requirement".to_string());
        return (MISMATCH_SEVERITY_INFO, reason_codes);
    }

    // Echo-based severity (data integrity risk)
    if save_echo_required && save_echo_completeness == "missing" {
        reason_codes.push("save_echo_missing".to_string());
        return (MISMATCH_SEVERITY_CRITICAL, reason_codes);
    }

    if save_echo_required && save_echo_completeness == "partial" {
        reason_codes.push("save_echo_partial".to_string());
        return (MISMATCH_SEVERITY_WARNING, reason_codes);
    }

    // Proof-verification mismatch with complete echo (protocol-level only)
    if mismatch_detected && save_echo_completeness == "complete" {
        reason_codes.push("proof_verification_failed_but_echo_complete".to_string());
        return (MISMATCH_SEVERITY_INFO, reason_codes);
    }

    // Proof-verification mismatch, echo not yet assessed (legacy/default path)
    if save_echo_required && save_echo_completeness == "not_assessed" {
        if mismatch_detected {
            reason_codes.push("proof_verification_failed_echo_not_assessed".to_string());
            return (MISMATCH_SEVERITY_CRITICAL, reason_codes);
        }
        // No mismatch and no echo assessment yet: keep neutral severity.
        return (MISMATCH_SEVERITY_NONE, reason_codes);
    }

    if mismatch_detected {
        reason_codes.push("proof_verification_failed".to_string());
        return (MISMATCH_SEVERITY_CRITICAL, reason_codes);
    }

    (MISMATCH_SEVERITY_NONE, reason_codes)
}

pub(super) const LEARNING_TELEMETRY_SCHEMA_VERSION: i64 = 1;
pub(super) const SAVE_HANDSHAKE_INVARIANT_ID: &str = "INV-002";

pub(super) fn stable_hash_suffix(seed: &str, chars: usize) -> String {
    let mut hasher = Sha256::new();
    hasher.update(seed.as_bytes());
    let digest = hex::encode(hasher.finalize());
    let end = chars.min(digest.len());
    digest[..end].to_string()
}

pub(super) fn pseudonymize_user_id_for_learning_signal(user_id: Uuid) -> String {
    let salt = std::env::var("KURA_TELEMETRY_SALT")
        .unwrap_or_else(|_| "kura-learning-telemetry-v1".to_string());
    let seed = format!("{salt}:{user_id}");
    format!("u_{}", stable_hash_suffix(&seed, 24))
}

pub(super) fn learning_signal_category(signal_type: &str) -> &'static str {
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
        "response_mode_selected" => "outcome_signal",
        "personal_failure_profile_observed" => "quality_signal",
        "retrieval_regret_observed" => "friction_signal",
        "laaj_sidecar_assessed" => "quality_signal",
        "counterfactual_recommendation_prepared" => "quality_signal",
        "post_task_reflection_confirmed" => "outcome_signal",
        "post_task_reflection_partial" => "friction_signal",
        "post_task_reflection_unresolved" => "friction_signal",
        "mismatch_detected" => "quality_signal",
        "mismatch_repaired" => "correction_signal",
        "mismatch_unresolved" => "friction_signal",
        _ => "quality_signal",
    }
}

pub(super) fn save_claim_confidence_band(claim_guard: &AgentWriteClaimGuard) -> &'static str {
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

pub(super) fn build_learning_signal_event(
    user_id: Uuid,
    signal_type: &str,
    issue_type: &str,
    claim_guard: &AgentWriteClaimGuard,
    verification: &AgentWriteVerificationSummary,
    requested_event_count: usize,
    receipt_count: usize,
    model_identity: &ResolvedModelIdentity,
    signal_severity: MismatchSeverity,
    mismatch_reason_codes: &[String],
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
            "mismatch_severity": signal_severity.severity,
            "mismatch_weight": signal_severity.weight,
            "mismatch_domain": signal_severity.domain,
            "mismatch_reason_codes": mismatch_reason_codes,
            "runtime_model_identity": model_identity.model_identity,
            "model_identity_source": model_identity.source,
            "model_attestation_request_id": model_identity.attestation_request_id,
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

pub(super) fn build_save_handshake_learning_signal_events(
    user_id: Uuid,
    requested_event_count: usize,
    receipts: &[AgentWriteReceipt],
    verification: &AgentWriteVerificationSummary,
    claim_guard: &AgentWriteClaimGuard,
    model_identity: &ResolvedModelIdentity,
) -> Vec<CreateEventRequest> {
    // Compute severity once for all signals in this write.
    let save_echo_required = matches!(
        claim_guard.claim_status.as_str(),
        "saved_verified" | "inferred"
    );
    let save_echo_completeness = if save_echo_required {
        "not_assessed"
    } else {
        "not_applicable"
    };
    let mismatch_detected = !claim_guard.allow_saved_claim;
    let (severity, reason_codes) = classify_mismatch_severity(
        mismatch_detected,
        save_echo_required,
        save_echo_completeness,
    );

    if claim_guard.allow_saved_claim {
        return vec![build_learning_signal_event(
            user_id,
            "save_handshake_verified",
            "save_handshake_verified",
            claim_guard,
            verification,
            requested_event_count,
            receipts.len(),
            model_identity,
            severity,
            &reason_codes,
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
            model_identity,
            severity,
            &reason_codes,
        ),
        build_learning_signal_event(
            user_id,
            "save_claim_mismatch_attempt",
            "save_claim_mismatch_attempt",
            claim_guard,
            verification,
            requested_event_count,
            receipts.len(),
            model_identity,
            severity,
            &reason_codes,
        ),
    ]
}
