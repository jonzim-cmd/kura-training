use super::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum WritePreflightBlockerCode {
    ReadAfterWriteTargetsRequired,
    FormalEventTypeMissing,
    FormalEventTypeNonCanonical,
    FormalEventTypeInvalidShape,
    FormalEventTypeUnknown,
    HealthConsentRequired,
    IntentHandshakeInvalid,
    TemporalBasisInvalid,
    IntentHandshakeRequired,
    WorkflowOnboardingBlocked,
    AutonomyGateBlocked,
    IntentHandshakeRequiredStrictTier,
    HighImpactConfirmationInvalid,
}

impl WritePreflightBlockerCode {
    pub(super) const fn as_str(self) -> &'static str {
        match self {
            Self::ReadAfterWriteTargetsRequired => "read_after_write_targets_required",
            Self::FormalEventTypeMissing => "formal_event_type_missing",
            Self::FormalEventTypeNonCanonical => "formal_event_type_non_canonical",
            Self::FormalEventTypeInvalidShape => "formal_event_type_invalid_shape",
            Self::FormalEventTypeUnknown => "formal_event_type_unknown",
            Self::HealthConsentRequired => "health_consent_required",
            Self::IntentHandshakeInvalid => "intent_handshake_invalid",
            Self::TemporalBasisInvalid => "temporal_basis_invalid",
            Self::IntentHandshakeRequired => "intent_handshake_required",
            Self::WorkflowOnboardingBlocked => "workflow_onboarding_blocked",
            Self::AutonomyGateBlocked => "autonomy_gate_blocked",
            Self::IntentHandshakeRequiredStrictTier => "intent_handshake_required_strict_tier",
            Self::HighImpactConfirmationInvalid => "high_impact_confirmation_invalid",
        }
    }
}

impl From<WritePreflightBlockerCode> for String {
    fn from(value: WritePreflightBlockerCode) -> Self {
        value.as_str().to_string()
    }
}

pub(super) fn has_preflight_blocker(
    blockers: &[AgentWritePreflightBlocker],
    code: WritePreflightBlockerCode,
    stage: &str,
) -> bool {
    blockers
        .iter()
        .any(|item| item.code == code.as_str() && item.stage == stage)
}

pub(super) fn push_preflight_blocker(
    blockers: &mut Vec<AgentWritePreflightBlocker>,
    code: impl Into<String>,
    stage: &str,
    message: impl Into<String>,
    field: Option<&str>,
    docs_hint: Option<String>,
    details: Option<Value>,
) {
    let normalized_code = code.into();
    if blockers.iter().any(|item| item.code == normalized_code) {
        return;
    }
    blockers.push(AgentWritePreflightBlocker {
        code: normalized_code,
        stage: stage.to_string(),
        message: message.into(),
        field: field.map(str::to_string),
        docs_hint,
        details,
    });
}

pub(super) fn push_preflight_blocker_from_error(
    blockers: &mut Vec<AgentWritePreflightBlocker>,
    code: impl Into<String>,
    stage: &str,
    error: &AppError,
) {
    let fallback_code = code.into();
    match error {
        AppError::Validation {
            message,
            field,
            received,
            docs_hint,
        } => {
            push_preflight_blocker(
                blockers,
                fallback_code.clone(),
                stage,
                message.clone(),
                field.as_deref(),
                docs_hint.clone(),
                received.clone(),
            );
        }
        AppError::PolicyViolation {
            code: policy_code,
            message,
            field,
            received,
            docs_hint,
        } => {
            let effective_code = if fallback_code.is_empty() {
                policy_code.clone()
            } else {
                fallback_code
            };
            push_preflight_blocker(
                blockers,
                effective_code,
                stage,
                message.clone(),
                field.as_deref(),
                docs_hint.clone(),
                received.clone(),
            );
        }
        other => {
            push_preflight_blocker(
                blockers,
                fallback_code,
                stage,
                format!("{other:?}"),
                None,
                None,
                None,
            );
        }
    }
}

pub(super) fn write_with_proof_preflight_error(
    blockers: Vec<AgentWritePreflightBlocker>,
) -> AppError {
    let preflight = AgentWritePreflightSummary {
        schema_version: AGENT_WRITE_PREFLIGHT_SCHEMA_VERSION.to_string(),
        status: "blocked".to_string(),
        blockers: blockers.clone(),
    };
    AppError::Validation {
        message: AGENT_WRITE_PREFLIGHT_BLOCKED_MESSAGE.to_string(),
        field: Some("events".to_string()),
        received: Some(json!(preflight)),
        docs_hint: Some(
            "Resolve all listed blockers before retrying. Do not assume partial writes were persisted."
                .to_string(),
        ),
    }
}
