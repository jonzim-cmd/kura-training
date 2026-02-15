use super::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum SystemConfigFieldClass {
    PublicContract,
    SensitiveGuidance,
    InternalStrategy,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum SystemConventionFieldClass {
    PublicContract,
    InternalOperations,
    Unknown,
}

pub(super) fn classify_system_config_field(key: &str) -> SystemConfigFieldClass {
    match key {
        "dimensions" | "event_conventions" | "projection_schemas" | "conventions" => {
            SystemConfigFieldClass::PublicContract
        }
        "interview_guide" => SystemConfigFieldClass::SensitiveGuidance,
        "agent_behavior" => SystemConfigFieldClass::InternalStrategy,
        _ => SystemConfigFieldClass::Unknown,
    }
}

pub(super) fn classify_system_convention_field(key: &str) -> SystemConventionFieldClass {
    match key {
        "exercise_normalization"
        | "training_core_fields_v1"
        | "training_session_block_model_v1"
        | "evidence_layer_v1"
        | "open_observation_v1"
        | "ingestion_locale_v1"
        | "load_context_v1"
        | "session_feedback_certainty_v1"
        | "schema_capability_gate_v1"
        | "model_tier_registry_v1"
        | "response_mode_policy_v1"
        | "personal_failure_profile_v1"
        | "sidecar_retrieval_regret_v1"
        | "advisory_scoring_layer_v1"
        | "counterfactual_recommendation_v1"
        | "synthetic_adversarial_corpus_v1"
        | "temporal_grounding_v1"
        | "decision_brief_v1"
        | "high_impact_plan_update_v1" => SystemConventionFieldClass::PublicContract,
        "learning_clustering_v1"
        | "extraction_calibration_v1"
        | "learning_backlog_bridge_v1"
        | "unknown_dimension_mining_v1"
        | "shadow_evaluation_gate_v1" => SystemConventionFieldClass::InternalOperations,
        _ => SystemConventionFieldClass::Unknown,
    }
}

pub(super) fn redact_system_conventions_for_agent(value: Value) -> Value {
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

pub(super) fn redact_system_config_data_for_agent(value: Value) -> Value {
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

pub(super) fn redact_system_config_for_agent(system: SystemConfigResponse) -> SystemConfigResponse {
    SystemConfigResponse {
        data: redact_system_config_data_for_agent(system.data),
        version: system.version,
        updated_at: system.updated_at,
    }
}

pub(super) fn build_agent_context_system_contract() -> AgentContextSystemContract {
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
