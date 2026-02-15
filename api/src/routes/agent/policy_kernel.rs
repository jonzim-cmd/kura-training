use super::*;

pub(super) fn response_mode_thresholds(signals: &RuntimeQualitySignals) -> (f64, f64) {
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

    if signals.outcome_signal_sample_ok {
        if signals.historical_regret_exceeded_rate_pct >= 40.0 {
            threshold_a += 0.04;
            threshold_b += 0.03;
        } else if signals.historical_regret_exceeded_rate_pct <= 12.0 {
            threshold_a -= 0.02;
            threshold_b -= 0.01;
        }

        if signals.historical_challenge_rate_pct >= 20.0 {
            threshold_a += 0.03;
            threshold_b += 0.02;
        } else if signals.historical_challenge_rate_pct <= 8.0 {
            threshold_a -= 0.01;
        }

        if signals.historical_follow_through_rate_pct >= 72.0 {
            threshold_a -= 0.02;
            threshold_b -= 0.01;
        } else if signals.historical_follow_through_rate_pct <= 38.0 {
            threshold_a += 0.03;
            threshold_b += 0.02;
        }

        if signals.historical_save_verified_rate_pct >= 88.0 {
            threshold_a -= 0.01;
            threshold_b -= 0.01;
        } else if signals.historical_save_verified_rate_pct <= 60.0 {
            threshold_a += 0.02;
            threshold_b += 0.01;
        }
    }

    (threshold_a.clamp(0.55, 0.95), threshold_b.clamp(0.25, 0.85))
}

pub(super) fn response_mode_evidence_score(
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

    if signals.outcome_signal_sample_ok {
        let challenge_penalty =
            (signals.historical_challenge_rate_pct / 100.0).clamp(0.0, 0.40) * 0.12;
        let regret_penalty =
            (signals.historical_regret_exceeded_rate_pct / 100.0).clamp(0.0, 0.60) * 0.16;
        let follow_delta =
            ((signals.historical_follow_through_rate_pct - 50.0) / 50.0).clamp(-1.0, 1.0);
        let save_delta =
            ((signals.historical_save_verified_rate_pct - 50.0) / 50.0).clamp(-1.0, 1.0);
        score -= challenge_penalty + regret_penalty;
        score += follow_delta * 0.07;
        score += save_delta * 0.05;
    }

    score.clamp(0.0, 1.0)
}

pub(super) fn build_response_mode_policy(
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
    if signals.outcome_signal_sample_ok {
        reason_codes.push("historical_outcome_tuning_applied".to_string());
        if signals.historical_regret_exceeded_rate_pct >= 40.0 {
            reason_codes.push("historical_high_regret_rate".to_string());
        }
        if signals.historical_challenge_rate_pct >= 20.0 {
            reason_codes.push("historical_high_challenge_rate".to_string());
        }
        if signals.historical_follow_through_rate_pct <= 38.0 {
            reason_codes.push("historical_low_follow_through_rate".to_string());
        }
    } else if signals.outcome_signal_sample_size > 0 {
        reason_codes.push("historical_outcome_sample_below_floor".to_string());
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
        outcome_signal_sample_size: signals.outcome_signal_sample_size,
        outcome_signal_sample_ok: signals.outcome_signal_sample_ok,
        outcome_signal_sample_confidence: signals.outcome_signal_sample_confidence,
        historical_follow_through_rate_pct: signals.historical_follow_through_rate_pct,
        historical_challenge_rate_pct: signals.historical_challenge_rate_pct,
        historical_regret_exceeded_rate_pct: signals.historical_regret_exceeded_rate_pct,
        historical_save_verified_rate_pct: signals.historical_save_verified_rate_pct,
        policy_role: RESPONSE_MODE_POLICY_ROLE_NUDGE_ONLY.to_string(),
        requires_transparency_note,
        reason_codes,
        assistant_instruction,
    }
}

pub(super) fn build_personal_failure_profile(
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

pub(super) fn regret_band(score: f64) -> &'static str {
    if score >= 0.66 {
        "high"
    } else if score >= 0.33 {
        "medium"
    } else {
        "low"
    }
}

pub(super) fn build_retrieval_regret(
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

pub(super) fn build_laaj_sidecar(
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

pub(super) fn build_sidecar_assessment(
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

pub(super) fn build_counterfactual_recommendation(
    claim_guard: &AgentWriteClaimGuard,
    verification: &AgentWriteVerificationSummary,
    response_mode_policy: &AgentResponseModePolicy,
    sidecar_assessment: &AgentSidecarAssessment,
) -> AgentCounterfactualRecommendation {
    let limited_evidence = response_mode_policy.mode_code != "A";
    let high_regret = sidecar_assessment.retrieval_regret.threshold_exceeded;
    let confirm_first = claim_guard.autonomy_gate.decision == "confirm_first";
    let transparency_level = if limited_evidence || high_regret || verification.status != "verified"
    {
        "uncertainty_explicit"
    } else {
        "evidence_anchored"
    };
    let mut reason_codes: Vec<String> = Vec::new();
    if limited_evidence {
        reason_codes.push("counterfactual_limited_evidence_context".to_string());
    }
    if high_regret {
        reason_codes.push("counterfactual_high_regret_context".to_string());
    }
    if confirm_first {
        reason_codes.push("counterfactual_confirm_first_gate".to_string());
    }
    if response_mode_policy.outcome_signal_sample_ok {
        reason_codes.push("counterfactual_history_sample_ok".to_string());
    } else if response_mode_policy.outcome_signal_sample_size > 0 {
        reason_codes.push("counterfactual_history_sample_below_floor".to_string());
    }
    dedupe_reason_codes(&mut reason_codes);

    let missing_evidence = vec![
        "Mehr persoenliche Verlaufssignale fuer diese Empfehlung.".to_string(),
        "Explizites Feedback, welche Annahme fuer dich unplausibel wirkt.".to_string(),
    ];
    let mut alternatives = if limited_evidence || high_regret {
        vec![
            AgentCounterfactualAlternative {
                option_id: "cf_collect_evidence".to_string(),
                title: "Erst Evidenz staerken, dann zuspitzen".to_string(),
                when_to_choose: "Wenn du Sicherheit vor Tempo priorisierst.".to_string(),
                tradeoff: "Weniger sofortige Personalisierung, dafuer geringeres Fehlrisiko."
                    .to_string(),
                missing_evidence: missing_evidence.clone(),
            },
            AgentCounterfactualAlternative {
                option_id: "cf_small_probe".to_string(),
                title: "Kleine Probe mit schneller Rueckmeldung".to_string(),
                when_to_choose: "Wenn du Momentum behalten und zuegig lernen willst.".to_string(),
                tradeoff: "Schnelleres Lernen, aber kurzfristig mehr Unsicherheit.".to_string(),
                missing_evidence,
            },
        ]
    } else {
        vec![
            AgentCounterfactualAlternative {
                option_id: "cf_accelerate".to_string(),
                title: "Etwas ambitionierterer naechster Schritt".to_string(),
                when_to_choose: "Wenn du Fortschritt beschleunigen willst und dich stabil fuehlst."
                    .to_string(),
                tradeoff: "Hoeherer Fortschrittshebel, aber etwas engeres Fehlerfenster."
                    .to_string(),
                missing_evidence: Vec::new(),
            },
            AgentCounterfactualAlternative {
                option_id: "cf_stabilize".to_string(),
                title: "Konservative Stabilisierung".to_string(),
                when_to_choose: "Wenn du Konstanz und Planbarkeit priorisierst.".to_string(),
                tradeoff: "Etwas langsamerer Fortschritt, dafuer robustere Ausfuehrung."
                    .to_string(),
                missing_evidence: Vec::new(),
            },
        ]
    };
    alternatives.truncate(2);

    let ask_user_challenge_question = limited_evidence || high_regret || confirm_first;
    let challenge_question = if ask_user_challenge_question {
        Some("Welche Annahme hinter meiner Empfehlung wuerdest du zuerst challengen?".to_string())
    } else {
        None
    };
    let explanation_summary = if transparency_level == "uncertainty_explicit" {
        "Ich zeige dir bewusst eine konservative und eine probe-basierte Alternative, damit du transparent zwischen Sicherheit und Tempo waehlen kannst.".to_string()
    } else {
        "Ich zeige dir eine beschleunigende und eine stabilisierende Alternative, damit die Entscheidung nachvollziehbar bleibt.".to_string()
    };

    AgentCounterfactualRecommendation {
        schema_version: COUNTERFACTUAL_RECOMMENDATION_SCHEMA_VERSION.to_string(),
        policy_role: SIDECAR_POLICY_ROLE_ADVISORY_ONLY.to_string(),
        rationale_style: "first_principles".to_string(),
        primary_recommendation_mode: response_mode_policy.mode.clone(),
        transparency_level: transparency_level.to_string(),
        explanation_summary,
        reason_codes,
        alternatives,
        ask_user_challenge_question,
        challenge_question,
        ux_compact: true,
    }
}

fn score_band(value: f64) -> &'static str {
    if value >= 0.66 {
        "high"
    } else if value >= 0.33 {
        "medium"
    } else {
        "low"
    }
}

pub(super) fn build_advisory_scores(
    claim_guard: &AgentWriteClaimGuard,
    verification: &AgentWriteVerificationSummary,
    session_audit: &AgentSessionAuditSummary,
    response_mode_policy: &AgentResponseModePolicy,
    sidecar_assessment: &AgentSidecarAssessment,
    persist_intent: &AgentPersistIntent,
) -> AgentAdvisoryScores {
    let mut specificity_reason_codes: Vec<String> = Vec::new();
    let mut hallucination_reason_codes: Vec<String> = Vec::new();
    let mut data_quality_reason_codes: Vec<String> = Vec::new();
    let mut confidence_reason_codes: Vec<String> = Vec::new();

    let mut specificity_score: f64 = 0.18;
    match response_mode_policy.mode_code.as_str() {
        "A" => {
            specificity_score += 0.46;
            specificity_reason_codes.push("response_mode_grounded_personalized".to_string());
        }
        "B" => {
            specificity_score += 0.28;
            specificity_reason_codes.push("response_mode_hypothesis_personalized".to_string());
        }
        _ => {
            specificity_score += 0.12;
            specificity_reason_codes.push("response_mode_general_guidance".to_string());
        }
    }
    match verification.status.as_str() {
        "verified" => {
            specificity_score += 0.16;
            specificity_reason_codes.push("verification_status_verified".to_string());
        }
        "pending" => {
            specificity_score += 0.06;
            specificity_reason_codes.push("verification_status_pending".to_string());
        }
        _ => {
            specificity_score -= 0.12;
            specificity_reason_codes.push("verification_status_failed".to_string());
        }
    }
    if claim_guard.allow_saved_claim {
        specificity_score += 0.18;
        specificity_reason_codes.push("claim_guard_saved_claim".to_string());
    } else {
        specificity_score -= 0.08;
        specificity_reason_codes.push("claim_guard_unsaved".to_string());
    }
    if response_mode_policy.outcome_signal_sample_ok {
        specificity_score += 0.08;
        specificity_reason_codes.push("outcome_signal_sample_ok".to_string());
    } else {
        specificity_score -= 0.04;
        specificity_reason_codes.push("outcome_signal_sample_thin".to_string());
    }
    if sidecar_assessment.retrieval_regret.threshold_exceeded {
        specificity_score -= 0.10;
        specificity_reason_codes.push("retrieval_regret_threshold_exceeded".to_string());
    }
    if session_audit.mismatch_unresolved > 0 {
        specificity_score -= 0.08;
        specificity_reason_codes.push("session_mismatch_unresolved".to_string());
    }
    specificity_score = specificity_score.clamp(0.0, 1.0);

    let mut hallucination_risk: f64 = 0.22;
    if !claim_guard.allow_saved_claim {
        hallucination_risk += 0.14;
        hallucination_reason_codes.push("save_claim_not_verified".to_string());
    }
    match verification.status.as_str() {
        "failed" => {
            hallucination_risk += 0.28;
            hallucination_reason_codes.push("write_proof_failed".to_string());
        }
        "pending" => {
            hallucination_risk += 0.10;
            hallucination_reason_codes.push("write_proof_pending".to_string());
        }
        _ => {}
    }
    match response_mode_policy.mode_code.as_str() {
        "C" => {
            hallucination_risk += 0.18;
            hallucination_reason_codes.push("response_mode_general_guidance".to_string());
        }
        "B" => {
            hallucination_risk += 0.08;
            hallucination_reason_codes.push("response_mode_hypothesis_personalized".to_string());
        }
        _ => {
            hallucination_risk -= 0.05;
            hallucination_reason_codes.push("response_mode_grounded_personalized".to_string());
        }
    }
    if sidecar_assessment.retrieval_regret.threshold_exceeded {
        hallucination_risk += 0.16;
        hallucination_reason_codes.push("retrieval_regret_high".to_string());
    }
    if sidecar_assessment.laaj.verdict != "pass" {
        hallucination_risk += 0.12;
        hallucination_reason_codes.push("laaj_verdict_review".to_string());
    }
    if session_audit.status == "needs_clarification" {
        hallucination_risk += 0.10;
        hallucination_reason_codes.push("session_audit_needs_clarification".to_string());
    }
    match response_mode_policy.calibration_status.as_str() {
        "monitor" => {
            hallucination_risk += 0.05;
            hallucination_reason_codes.push("calibration_monitor".to_string());
        }
        "degraded" => {
            hallucination_risk += 0.12;
            hallucination_reason_codes.push("calibration_degraded".to_string());
        }
        _ => {}
    }
    match response_mode_policy.integrity_slo_status.as_str() {
        "monitor" => {
            hallucination_risk += 0.04;
            hallucination_reason_codes.push("integrity_monitor".to_string());
        }
        "degraded" => {
            hallucination_risk += 0.10;
            hallucination_reason_codes.push("integrity_degraded".to_string());
        }
        _ => {}
    }
    if response_mode_policy.outcome_signal_sample_ok
        && response_mode_policy.historical_follow_through_rate_pct >= 70.0
        && response_mode_policy.historical_regret_exceeded_rate_pct <= 12.0
    {
        hallucination_risk -= 0.06;
        hallucination_reason_codes.push("historical_outcomes_stable".to_string());
    }
    hallucination_risk = hallucination_risk.clamp(0.0, 1.0);

    let mut data_quality_risk: f64 = 0.16;
    if !claim_guard.allow_saved_claim {
        data_quality_risk += 0.24;
        data_quality_reason_codes.push("claim_guard_unsaved".to_string());
    }
    match verification.status.as_str() {
        "failed" => {
            data_quality_risk += 0.26;
            data_quality_reason_codes.push("verification_failed".to_string());
        }
        "pending" => {
            data_quality_risk += 0.12;
            data_quality_reason_codes.push("verification_pending".to_string());
        }
        _ => {
            data_quality_risk -= 0.04;
            data_quality_reason_codes.push("verification_verified".to_string());
        }
    }
    if session_audit.mismatch_unresolved > 0 {
        data_quality_risk += 0.20;
        data_quality_reason_codes.push("session_mismatch_unresolved".to_string());
    }
    if session_audit.status == "needs_clarification" {
        data_quality_risk += 0.08;
        data_quality_reason_codes.push("session_audit_needs_clarification".to_string());
    }
    match persist_intent.mode.as_str() {
        "ask_first" => {
            data_quality_risk += 0.14;
            data_quality_reason_codes.push("persist_intent_ask_first".to_string());
        }
        "auto_draft" => {
            data_quality_risk += 0.08;
            data_quality_reason_codes.push("persist_intent_auto_draft".to_string());
        }
        "auto_save" => {
            data_quality_risk -= 0.04;
            data_quality_reason_codes.push("persist_intent_auto_save".to_string());
        }
        _ => {}
    }
    match persist_intent.status_label.as_str() {
        "not_saved" => {
            data_quality_risk += 0.12;
            data_quality_reason_codes.push("persist_status_not_saved".to_string());
        }
        "saved" => {
            data_quality_risk -= 0.05;
            data_quality_reason_codes.push("persist_status_saved".to_string());
        }
        _ => {}
    }
    if persist_intent.draft_event_count > 0 {
        data_quality_risk += 0.04;
        data_quality_reason_codes.push("persist_draft_events_present".to_string());
    }
    if sidecar_assessment.retrieval_regret.threshold_exceeded {
        data_quality_risk += 0.06;
        data_quality_reason_codes.push("retrieval_regret_high".to_string());
    }
    data_quality_risk = data_quality_risk.clamp(0.0, 1.0);

    let mut confidence_score: f64 =
        (specificity_score + (1.0 - hallucination_risk) + (1.0 - data_quality_risk)) / 3.0;
    confidence_reason_codes.push("confidence_from_specificity_and_risk_balance".to_string());
    match response_mode_policy.mode_code.as_str() {
        "A" => {
            confidence_score += 0.05;
            confidence_reason_codes.push("response_mode_grounded_bonus".to_string());
        }
        "C" => {
            confidence_score -= 0.05;
            confidence_reason_codes.push("response_mode_general_penalty".to_string());
        }
        _ => {}
    }
    match verification.status.as_str() {
        "verified" => {
            confidence_score += 0.03;
            confidence_reason_codes.push("verification_verified_bonus".to_string());
        }
        "failed" => {
            confidence_score -= 0.08;
            confidence_reason_codes.push("verification_failed_penalty".to_string());
        }
        _ => {}
    }
    match response_mode_policy.outcome_signal_sample_confidence.as_str() {
        "high" => {
            confidence_score += 0.04;
            confidence_reason_codes.push("outcome_sample_confidence_high".to_string());
        }
        "medium" => {
            confidence_score += 0.01;
            confidence_reason_codes.push("outcome_sample_confidence_medium".to_string());
        }
        _ => {
            confidence_score -= 0.02;
            confidence_reason_codes.push("outcome_sample_confidence_low".to_string());
        }
    }
    if session_audit.status == "needs_clarification" {
        confidence_score -= 0.08;
        confidence_reason_codes.push("session_audit_clarification_penalty".to_string());
    }
    confidence_score = confidence_score.clamp(0.0, 1.0);

    dedupe_reason_codes(&mut specificity_reason_codes);
    dedupe_reason_codes(&mut hallucination_reason_codes);
    dedupe_reason_codes(&mut data_quality_reason_codes);
    dedupe_reason_codes(&mut confidence_reason_codes);

    AgentAdvisoryScores {
        schema_version: ADVISORY_SCORING_LAYER_SCHEMA_VERSION.to_string(),
        policy_role: SIDECAR_POLICY_ROLE_ADVISORY_ONLY.to_string(),
        specificity_score,
        hallucination_risk,
        data_quality_risk,
        confidence_score,
        specificity_band: score_band(specificity_score).to_string(),
        hallucination_risk_band: score_band(hallucination_risk).to_string(),
        data_quality_risk_band: score_band(data_quality_risk).to_string(),
        confidence_band: score_band(confidence_score).to_string(),
        specificity_reason_codes,
        hallucination_reason_codes,
        data_quality_reason_codes,
        confidence_reason_codes,
    }
}

pub(super) fn build_advisory_action_plan(
    claim_guard: &AgentWriteClaimGuard,
    response_mode_policy: &AgentResponseModePolicy,
    persist_intent: &AgentPersistIntent,
    advisory_scores: &AgentAdvisoryScores,
) -> AgentAdvisoryActionPlan {
    let mut reason_codes: Vec<String> = Vec::new();

    let mut response_mode_hint = if advisory_scores.specificity_score >= 0.72
        && advisory_scores.hallucination_risk <= 0.40
        && advisory_scores.data_quality_risk <= 0.42
    {
        "grounded_personalized".to_string()
    } else if advisory_scores.hallucination_risk >= 0.65
        || advisory_scores.confidence_score <= 0.45
        || advisory_scores.data_quality_risk >= 0.62
    {
        "general_guidance".to_string()
    } else {
        "hypothesis_personalized".to_string()
    };
    if response_mode_hint != response_mode_policy.mode {
        reason_codes.push("response_mode_hint_adjusted_by_advisory_scores".to_string());
    }

    let mut persist_action = if advisory_scores.data_quality_risk >= 0.72
        || advisory_scores.hallucination_risk >= 0.72
    {
        "ask_first".to_string()
    } else if advisory_scores.data_quality_risk >= 0.48 || !claim_guard.allow_saved_claim {
        "draft_preferred".to_string()
    } else {
        "persist_now".to_string()
    };

    if persist_intent.mode == "ask_first" {
        persist_action = "ask_first".to_string();
        reason_codes.push("persist_intent_requires_ask_first".to_string());
    } else if persist_intent.mode == "auto_draft" && persist_action == "persist_now" {
        persist_action = "draft_preferred".to_string();
        reason_codes.push("persist_intent_prefers_draft".to_string());
    }
    if persist_intent.status_label == "not_saved" && persist_action == "persist_now" {
        persist_action = "draft_preferred".to_string();
        reason_codes.push("persist_status_not_saved".to_string());
    }

    let clarification_question_budget =
        usize::from(advisory_scores.hallucination_risk >= 0.55 || advisory_scores.data_quality_risk >= 0.55);
    if clarification_question_budget == 1 {
        reason_codes.push("clarification_budget_enabled_by_risk".to_string());
    }

    let requires_uncertainty_note = response_mode_policy.requires_transparency_note
        || advisory_scores.hallucination_risk >= 0.45
        || advisory_scores.confidence_score < 0.62;
    if requires_uncertainty_note {
        reason_codes.push("uncertainty_note_required".to_string());
    }

    if response_mode_hint == "general_guidance" {
        reason_codes.push("response_mode_hint_general_guidance".to_string());
    } else if response_mode_hint == "grounded_personalized" {
        reason_codes.push("response_mode_hint_grounded_personalized".to_string());
    } else {
        reason_codes.push("response_mode_hint_hypothesis_personalized".to_string());
    }
    if persist_action == "ask_first" {
        reason_codes.push("persist_action_ask_first".to_string());
    } else if persist_action == "draft_preferred" {
        reason_codes.push("persist_action_draft_preferred".to_string());
    } else {
        reason_codes.push("persist_action_persist_now".to_string());
    }

    let assistant_instruction = match (response_mode_hint.as_str(), persist_action.as_str()) {
        ("grounded_personalized", "persist_now") => {
            "Deliver evidence-anchored personalization and proceed with persistence normally."
        }
        ("general_guidance", "ask_first") => {
            "Stay conservative: disclose uncertainty, ask one focused clarification, and request explicit save confirmation."
        }
        ("general_guidance", _) => {
            "Favor general guidance with explicit uncertainty and one high-value clarification before specific claims."
        }
        (_, "ask_first") => {
            "Keep personalization cautious and ask explicit save confirmation before persisting."
        }
        (_, "draft_preferred") => {
            "Use hypothesis-style personalization and prefer draft persistence until evidence strengthens."
        }
        _ => {
            "Use hypothesis-driven personalization with explicit evidence boundaries."
        }
    }
    .to_string();

    if response_mode_hint == "general_guidance" && response_mode_policy.mode_code == "A" {
        response_mode_hint = "hypothesis_personalized".to_string();
        reason_codes.push("grounded_mode_relaxed_to_hypothesis_for_consistency".to_string());
    }

    dedupe_reason_codes(&mut reason_codes);

    AgentAdvisoryActionPlan {
        schema_version: ADVISORY_ACTION_PLAN_SCHEMA_VERSION.to_string(),
        policy_role: SIDECAR_POLICY_ROLE_ADVISORY_ONLY.to_string(),
        response_mode_hint,
        persist_action,
        clarification_question_budget,
        requires_uncertainty_note,
        assistant_instruction,
        reason_codes,
    }
}
