use std::collections::{HashMap, HashSet};

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::http::{HeaderMap, HeaderValue, header::HeaderName};
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::Value;
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::events::{
    BatchCreateEventsRequest, BatchCreateEventsResponse, BatchEventWarning, CreateEventRequest,
    CreateEventResponse, Event, EventMetadata, EventWarning, PaginatedResponse, ProjectionImpact,
    ProjectionImpactChange, SimulateEventsRequest, SimulateEventsResponse,
};

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::privacy::get_or_create_analysis_subject_id;
use crate::state::AppState;

pub fn write_router() -> Router<AppState> {
    Router::new()
        .route("/v1/events", post(create_event))
        .route("/v1/events/batch", post(create_events_batch))
        .route("/v1/events/simulate", post(simulate_events))
}

pub fn read_router() -> Router<AppState> {
    Router::new().route("/v1/events", get(list_events))
}

const HEALTH_CONSENT_ERROR_CODE: &str = "health_consent_required";
const HEALTH_CONSENT_NEXT_ACTION: &str = "open_settings_privacy";
const HEALTH_CONSENT_SETTINGS_URL: &str = "/settings?section=privacy";

fn health_consent_required_error() -> AppError {
    AppError::ForbiddenAction {
        message: "Processing health-related training data requires explicit consent (Art. 9 GDPR)."
            .to_string(),
        docs_hint: Some(
            "Grant consent in Settings > Privacy & Data before creating health/training events."
                .to_string(),
        ),
        error_code: Some(HEALTH_CONSENT_ERROR_CODE.to_string()),
        next_action: Some(HEALTH_CONSENT_NEXT_ACTION.to_string()),
        next_action_url: Some(HEALTH_CONSENT_SETTINGS_URL.to_string()),
    }
}

fn event_requires_health_data_consent(event_type: &str) -> bool {
    if matches!(
        event_type,
        "set.logged"
            | "set.corrected"
            | "event.retracted"
            | "session.logged"
            | "session.completed"
            | "bodyweight.logged"
            | "measurement.logged"
            | "meal.logged"
            | "sleep.logged"
            | "soreness.logged"
            | "energy.logged"
            | "training_plan.created"
            | "training_plan.updated"
            | "exercise.alias_created"
    ) {
        return true;
    }

    let normalized = event_type.trim().to_ascii_lowercase();
    normalized.starts_with("sleep.")
        || normalized.starts_with("recovery.")
        || normalized.starts_with("pain.")
        || normalized.starts_with("health.")
        || normalized.starts_with("nutrition.")
}

fn batch_requires_health_data_consent(events: &[CreateEventRequest]) -> bool {
    events
        .iter()
        .any(|event| event_requires_health_data_consent(&event.event_type))
}

async fn ensure_health_data_processing_consent(
    state: &AppState,
    user_id: Uuid,
) -> Result<(), AppError> {
    let consent = sqlx::query_scalar::<_, bool>(
        "SELECT consent_health_data_processing FROM users WHERE id = $1",
    )
    .bind(user_id)
    .fetch_optional(&state.db)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::Unauthorized {
        message: "Account not found".to_string(),
        docs_hint: None,
    })?;

    if !consent {
        return Err(health_consent_required_error());
    }

    Ok(())
}

/// Validate a single event request
fn validate_event(req: &CreateEventRequest) -> Result<(), AppError> {
    if req.event_type.is_empty() {
        return Err(AppError::Validation {
            message: "event_type must not be empty".to_string(),
            field: Some("event_type".to_string()),
            received: Some(serde_json::Value::String(req.event_type.clone())),
            docs_hint: Some(
                "event_type is a free-form string like 'set.logged', 'meal.logged', 'metric.logged'"
                    .to_string(),
            ),
        });
    }

    if req.metadata.idempotency_key.is_empty() {
        return Err(AppError::Validation {
            message: "metadata.idempotency_key must not be empty".to_string(),
            field: Some("metadata.idempotency_key".to_string()),
            received: None,
            docs_hint: Some(
                "Generate a unique idempotency_key per event (e.g. a UUID). \
                 This allows safe retries without duplicate events."
                    .to_string(),
            ),
        });
    }

    validate_critical_invariants(req)?;

    Ok(())
}

fn policy_violation(
    code: &str,
    message: impl Into<String>,
    field: Option<&str>,
    received: Option<serde_json::Value>,
    docs_hint: Option<&str>,
) -> AppError {
    AppError::PolicyViolation {
        code: code.to_string(),
        message: message.into(),
        field: field.map(str::to_string),
        received,
        docs_hint: docs_hint.map(str::to_string),
    }
}

fn non_empty_string_field(data: &serde_json::Value, key: &str) -> Option<String> {
    data.get(key)
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn non_empty_string_array(data: &serde_json::Value, key: &str) -> Option<Vec<String>> {
    let raw = data.get(key)?.as_array()?;
    let mut values = Vec::with_capacity(raw.len());
    for item in raw {
        let value = item.as_str()?.trim();
        if value.is_empty() {
            return None;
        }
        values.push(value.to_string());
    }
    if values.is_empty() {
        return None;
    }
    Some(values)
}

fn validate_critical_invariants(req: &CreateEventRequest) -> Result<(), AppError> {
    match req.event_type.as_str() {
        "event.retracted" => validate_retraction_invariants(&req.data),
        "set.corrected" => validate_set_correction_invariants(&req.data),
        "set.logged" => validate_set_logged_intensity_invariants(&req.data),
        "session.logged" => validate_session_logged_invariants(&req.data),
        "training_plan.created" => {
            validate_training_plan_invariants("training_plan.created", &req.data)
        }
        "training_plan.updated" => {
            validate_training_plan_invariants("training_plan.updated", &req.data)
        }
        "projection_rule.created" => validate_projection_rule_created_invariants(&req.data),
        "projection_rule.archived" => validate_projection_rule_archived_invariants(&req.data),
        _ => Ok(()),
    }
}

fn parse_decimal_string(raw: &str) -> Option<f64> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    let normalized = if trimmed.contains(',') && trimmed.contains('.') {
        let comma_idx = trimmed.rfind(',')?;
        let dot_idx = trimmed.rfind('.')?;
        if comma_idx > dot_idx {
            trimmed.replace('.', "").replace(',', ".")
        } else {
            trimmed.replace(',', "")
        }
    } else if trimmed.contains(',') {
        trimmed.replace(',', ".")
    } else {
        trimmed.to_string()
    };
    normalized.parse::<f64>().ok()
}

fn parse_flexible_float(value: &Value) -> Option<f64> {
    match value {
        Value::Number(number) => number.as_f64(),
        Value::String(text) => parse_decimal_string(text),
        _ => None,
    }
}

fn parse_optional_numeric_field(
    data: &Value,
    field_path: &str,
    invalid_code: &str,
    docs_hint: &str,
) -> Result<Option<f64>, AppError> {
    let field_name = field_path.rsplit('.').next().unwrap_or(field_path);
    let Some(value) = data.get(field_name) else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    let Some(parsed) = parse_flexible_float(value) else {
        return Err(policy_violation(
            invalid_code,
            format!("{field_path} must be numeric"),
            Some(field_path),
            Some(value.clone()),
            Some(docs_hint),
        ));
    };
    Ok(Some(parsed))
}

fn ensure_numeric_range(
    value: f64,
    field_path: &str,
    range_code: &str,
    min_value: f64,
    max_value: f64,
    docs_hint: &str,
) -> Result<(), AppError> {
    if value < min_value || value > max_value {
        return Err(policy_violation(
            range_code,
            format!(
                "{field_path}={} outside required range [{}, {}]",
                value, min_value, max_value
            ),
            Some(field_path),
            Some(serde_json::json!(value)),
            Some(docs_hint),
        ));
    }
    Ok(())
}

fn validate_set_logged_intensity_invariants(data: &Value) -> Result<(), AppError> {
    let rpe = parse_optional_numeric_field(
        data,
        "data.rpe",
        "inv_set_rpe_invalid_type",
        "Provide rpe as number (1..10). Locale decimals like '8,5' are accepted.",
    )?;
    if let Some(value) = rpe {
        ensure_numeric_range(
            value,
            "data.rpe",
            "inv_set_rpe_out_of_range",
            1.0,
            10.0,
            "Use an RPE value between 1 and 10.",
        )?;
    }

    let rir = parse_optional_numeric_field(
        data,
        "data.rir",
        "inv_set_rir_invalid_type",
        "Provide rir as number (0..10). Locale decimals like '2,5' are accepted.",
    )?;
    if let Some(value) = rir {
        ensure_numeric_range(
            value,
            "data.rir",
            "inv_set_rir_out_of_range",
            0.0,
            10.0,
            "Use an RIR value between 0 and 10.",
        )?;
    }

    Ok(())
}

fn training_plan_policy_violation(
    event_type: &str,
    code: &str,
    message: impl Into<String>,
    field: Option<&str>,
    received: Option<serde_json::Value>,
    docs_hint: Option<&str>,
) -> AppError {
    let message = message.into();
    tracing::warn!(
        event_type,
        policy_code = code,
        field = field.unwrap_or(""),
        "Rejected training_plan payload invariant: {}",
        message
    );
    policy_violation(code, message, field, received, docs_hint)
}

fn validate_training_plan_invariants(event_type: &str, data: &Value) -> Result<(), AppError> {
    if let Some(plan_id_raw) = data.get("plan_id") {
        if !plan_id_raw
            .as_str()
            .map(str::trim)
            .is_some_and(|value| !value.is_empty())
        {
            return Err(training_plan_policy_violation(
                event_type,
                "inv_training_plan_plan_id_invalid",
                "data.plan_id must be a non-empty string when provided",
                Some("data.plan_id"),
                Some(plan_id_raw.clone()),
                Some("Provide a stable plan_id, e.g. 'offseason_block_a'."),
            ));
        }
    }

    if let Some(name_raw) = data.get("name") {
        if !name_raw
            .as_str()
            .map(str::trim)
            .is_some_and(|value| !value.is_empty())
        {
            return Err(training_plan_policy_violation(
                event_type,
                "inv_training_plan_name_invalid",
                "data.name must be a non-empty string when provided",
                Some("data.name"),
                Some(name_raw.clone()),
                Some("Provide a descriptive plan name like '3x Full Body'."),
            ));
        }
    }

    let Some(sessions) = data.get("sessions") else {
        if event_type == "training_plan.created" {
            return Err(training_plan_policy_violation(
                event_type,
                "inv_training_plan_sessions_required",
                "training_plan.created requires data.sessions with at least one session",
                Some("data.sessions"),
                None,
                Some(
                    "Set data.sessions to a non-empty array of session objects, or send training_plan.updated for partial plan edits.",
                ),
            ));
        }
        return Ok(());
    };
    let Some(session_rows) = sessions.as_array() else {
        return Err(training_plan_policy_violation(
            event_type,
            "inv_training_plan_sessions_invalid",
            "data.sessions must be an array when provided",
            Some("data.sessions"),
            Some(sessions.clone()),
            Some("Use sessions as an array of session objects."),
        ));
    };
    if session_rows.is_empty() {
        return Err(training_plan_policy_violation(
            event_type,
            "inv_training_plan_sessions_empty",
            "data.sessions must include at least one session when provided",
            Some("data.sessions"),
            Some(sessions.clone()),
            Some(
                "Provide at least one session object, or omit data.sessions on training_plan.updated for metadata-only edits.",
            ),
        ));
    }

    for (session_idx, session) in session_rows.iter().enumerate() {
        let Some(session_obj) = session.as_object() else {
            continue;
        };
        let Some(exercises) = session_obj.get("exercises") else {
            continue;
        };
        let Some(exercise_rows) = exercises.as_array() else {
            let field = format!("data.sessions[{session_idx}].exercises");
            return Err(training_plan_policy_violation(
                event_type,
                "inv_training_plan_exercises_invalid",
                format!("{field} must be an array when provided"),
                Some(field.as_str()),
                Some(exercises.clone()),
                Some("Use exercises as an array of exercise objects."),
            ));
        };

        for (exercise_idx, exercise) in exercise_rows.iter().enumerate() {
            let Some(exercise_obj) = exercise.as_object() else {
                continue;
            };
            for (field_key, invalid_code, range_code, min_v, max_v, docs_hint) in [
                (
                    "target_rpe",
                    "inv_training_plan_target_rpe_invalid_type",
                    "inv_training_plan_target_rpe_out_of_range",
                    1.0,
                    10.0,
                    "Set target_rpe between 1 and 10.",
                ),
                (
                    "rpe",
                    "inv_training_plan_rpe_invalid_type",
                    "inv_training_plan_rpe_out_of_range",
                    1.0,
                    10.0,
                    "Set rpe between 1 and 10.",
                ),
                (
                    "target_rir",
                    "inv_training_plan_target_rir_invalid_type",
                    "inv_training_plan_target_rir_out_of_range",
                    0.0,
                    10.0,
                    "Set target_rir between 0 and 10.",
                ),
                (
                    "rir",
                    "inv_training_plan_rir_invalid_type",
                    "inv_training_plan_rir_out_of_range",
                    0.0,
                    10.0,
                    "Set rir between 0 and 10.",
                ),
            ] {
                let Some(raw) = exercise_obj.get(field_key) else {
                    continue;
                };
                let field_path =
                    format!("data.sessions[{session_idx}].exercises[{exercise_idx}].{field_key}");
                let Some(parsed) = parse_flexible_float(raw) else {
                    return Err(training_plan_policy_violation(
                        event_type,
                        invalid_code,
                        format!("{field_path} must be numeric"),
                        Some(field_path.as_str()),
                        Some(raw.clone()),
                        Some(
                            "Provide intensity fields as numbers. Locale decimals like '7,5' are accepted.",
                        ),
                    ));
                };
                ensure_numeric_range(parsed, &field_path, range_code, min_v, max_v, docs_hint)?;
            }
        }
    }

    Ok(())
}

const SESSION_LOGGED_CONTRACT_VERSION_V1: &str = "session.logged.v1";
const SESSION_BLOCK_TYPES: [&str; 11] = [
    "strength_set",
    "explosive_power",
    "plyometric_reactive",
    "sprint_accel_maxv",
    "speed_endurance",
    "interval_endurance",
    "continuous_endurance",
    "tempo_threshold",
    "circuit_hybrid",
    "technique_coordination",
    "recovery_session",
];
const SESSION_MEASUREMENT_STATES: [&str; 5] = [
    "measured",
    "estimated",
    "inferred",
    "not_measured",
    "not_applicable",
];

fn is_performance_block_type(block_type: &str) -> bool {
    block_type != "recovery_session"
}

fn validate_measurement_object(
    payload: &Value,
    field_path: &str,
    missing_state_code: &str,
    invalid_state_code: &str,
    missing_value_code: &str,
) -> Result<(), AppError> {
    let Some(obj) = payload.as_object() else {
        return Err(policy_violation(
            invalid_state_code,
            format!("{field_path} must be an object"),
            Some(field_path),
            Some(payload.clone()),
            Some("Use {measurement_state, value?, unit?, reference?}."),
        ));
    };

    let raw_state = obj
        .get("measurement_state")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            policy_violation(
                missing_state_code,
                format!("{field_path}.measurement_state is required"),
                Some(field_path),
                Some(payload.clone()),
                Some("Use one of: measured, estimated, inferred, not_measured, not_applicable."),
            )
        })?;

    let normalized_state = raw_state.to_lowercase();
    if !SESSION_MEASUREMENT_STATES.contains(&normalized_state.as_str()) {
        return Err(policy_violation(
            invalid_state_code,
            format!(
                "{field_path}.measurement_state '{}' is invalid",
                normalized_state
            ),
            Some(field_path),
            Some(Value::String(normalized_state)),
            Some("Use one of: measured, estimated, inferred, not_measured, not_applicable."),
        ));
    }

    if matches!(
        normalized_state.as_str(),
        "measured" | "estimated" | "inferred"
    ) {
        let has_value = obj.get("value").is_some_and(|value| !value.is_null());
        let has_reference = obj.get("reference").is_some_and(|value| !value.is_null());
        if !has_value && !has_reference {
            return Err(policy_violation(
                missing_value_code,
                format!(
                    "{field_path} requires value or reference when measurement_state is {}",
                    normalized_state
                ),
                Some(field_path),
                Some(payload.clone()),
                Some("Provide value (or reference) for measured/estimated/inferred measurements."),
            ));
        }
    }

    Ok(())
}

fn validate_dose_slice(payload: &Value, field_path: &str) -> Result<(), AppError> {
    let Some(obj) = payload.as_object() else {
        return Err(policy_violation(
            "inv_session_block_dose_invalid",
            format!("{field_path} must be an object"),
            Some(field_path),
            Some(payload.clone()),
            Some(
                "Use a dose object with duration_seconds, distance_meters, reps, and/or contacts.",
            ),
        ));
    };

    let mut has_dimension = false;
    for key in ["duration_seconds", "distance_meters", "reps", "contacts"] {
        let Some(raw) = obj.get(key) else {
            continue;
        };
        if raw.is_null() {
            continue;
        }
        let Some(parsed) = parse_flexible_float(raw) else {
            let full_field = format!("{field_path}.{key}");
            return Err(policy_violation(
                "inv_session_block_dose_dimension_invalid",
                format!("{full_field} must be numeric when provided"),
                Some(full_field.as_str()),
                Some(raw.clone()),
                Some(
                    "Provide dose dimensions as numbers (locale decimals like '2,5' are accepted).",
                ),
            ));
        };
        if parsed < 0.0 {
            let full_field = format!("{field_path}.{key}");
            return Err(policy_violation(
                "inv_session_block_dose_dimension_negative",
                format!("{full_field} must be >= 0"),
                Some(full_field.as_str()),
                Some(raw.clone()),
                Some("Dose dimensions cannot be negative."),
            ));
        }
        has_dimension = true;
    }

    if !has_dimension {
        return Err(policy_violation(
            "inv_session_block_work_dimension_required",
            format!("{field_path} requires at least one work dimension"),
            Some(field_path),
            Some(payload.clone()),
            Some("Provide at least one of duration_seconds, distance_meters, reps, contacts."),
        ));
    }

    Ok(())
}

fn validate_session_logged_invariants(data: &Value) -> Result<(), AppError> {
    let contract_version = non_empty_string_field(data, "contract_version").ok_or_else(|| {
        policy_violation(
            "inv_session_contract_version_required",
            "session.logged requires data.contract_version",
            Some("data.contract_version"),
            data.get("contract_version").cloned(),
            Some("Set contract_version to 'session.logged.v1'."),
        )
    })?;

    if contract_version != SESSION_LOGGED_CONTRACT_VERSION_V1 {
        return Err(policy_violation(
            "inv_session_contract_version_unsupported",
            format!(
                "session.logged supports contract_version='{}' only",
                SESSION_LOGGED_CONTRACT_VERSION_V1
            ),
            Some("data.contract_version"),
            Some(Value::String(contract_version)),
            Some("Use contract_version='session.logged.v1'."),
        ));
    }

    let session_meta = data
        .get("session_meta")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            policy_violation(
                "inv_session_meta_required",
                "session.logged requires data.session_meta object",
                Some("data.session_meta"),
                data.get("session_meta").cloned(),
                Some("Provide session_meta with sport/timezone/session context."),
            )
        })?;

    if let (Some(started_raw), Some(ended_raw)) = (
        session_meta.get("started_at").and_then(Value::as_str),
        session_meta.get("ended_at").and_then(Value::as_str),
    ) {
        let started = DateTime::parse_from_rfc3339(started_raw).map_err(|_| {
            policy_violation(
                "inv_session_meta_started_at_invalid",
                "session_meta.started_at must be ISO-8601 timestamp",
                Some("data.session_meta.started_at"),
                Some(Value::String(started_raw.to_string())),
                Some("Example: 2026-02-14T10:30:00+01:00"),
            )
        })?;
        let ended = DateTime::parse_from_rfc3339(ended_raw).map_err(|_| {
            policy_violation(
                "inv_session_meta_ended_at_invalid",
                "session_meta.ended_at must be ISO-8601 timestamp",
                Some("data.session_meta.ended_at"),
                Some(Value::String(ended_raw.to_string())),
                Some("Example: 2026-02-14T11:45:00+01:00"),
            )
        })?;
        if ended < started {
            return Err(policy_violation(
                "inv_session_meta_temporal_order",
                "session_meta.ended_at must be >= session_meta.started_at",
                Some("data.session_meta.ended_at"),
                Some(Value::String(ended_raw.to_string())),
                Some("Ensure session end timestamp is not earlier than start timestamp."),
            ));
        }
    }

    let blocks = data
        .get("blocks")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            policy_violation(
                "inv_session_blocks_required",
                "session.logged requires data.blocks as a non-empty array",
                Some("data.blocks"),
                data.get("blocks").cloned(),
                Some("Provide one or more block objects."),
            )
        })?;

    if blocks.is_empty() {
        return Err(policy_violation(
            "inv_session_blocks_empty",
            "session.logged requires at least one block",
            Some("data.blocks"),
            Some(Value::Array(vec![])),
            Some("Provide at least one block in data.blocks."),
        ));
    }

    for (index, block) in blocks.iter().enumerate() {
        let block_path = format!("data.blocks[{index}]");
        let Some(block_obj) = block.as_object() else {
            return Err(policy_violation(
                "inv_session_block_invalid",
                format!("{block_path} must be an object"),
                Some(block_path.as_str()),
                Some(block.clone()),
                Some("Provide block as {block_type, dose, intensity_anchors?, metrics?}."),
            ));
        };

        let block_type = block_obj
            .get("block_type")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .ok_or_else(|| {
                policy_violation(
                    "inv_session_block_type_required",
                    format!("{block_path}.block_type is required"),
                    Some(block_path.as_str()),
                    Some(block.clone()),
                    Some("Use a supported block_type (e.g. strength_set, interval_endurance)."),
                )
            })?
            .to_lowercase();

        if !SESSION_BLOCK_TYPES.contains(&block_type.as_str()) {
            let field = format!("{block_path}.block_type");
            return Err(policy_violation(
                "inv_session_block_type_unknown",
                format!("{field} '{}' is not supported", block_type),
                Some(field.as_str()),
                Some(Value::String(block_type)),
                Some("Use one of the published session.logged block types."),
            ));
        }

        let dose = block_obj.get("dose").ok_or_else(|| {
            policy_violation(
                "inv_session_block_dose_required",
                format!("{block_path}.dose is required"),
                Some(block_path.as_str()),
                Some(block.clone()),
                Some("Provide dose.work and optional dose.recovery/dose.repeats."),
            )
        })?;
        let dose_obj = dose.as_object().ok_or_else(|| {
            policy_violation(
                "inv_session_block_dose_invalid",
                format!("{block_path}.dose must be an object"),
                Some(block_path.as_str()),
                Some(dose.clone()),
                Some("Provide dose as object."),
            )
        })?;

        let work = dose_obj.get("work").ok_or_else(|| {
            policy_violation(
                "inv_session_block_work_required",
                format!("{block_path}.dose.work is required"),
                Some(block_path.as_str()),
                Some(dose.clone()),
                Some("Provide dose.work with at least one work dimension."),
            )
        })?;
        validate_dose_slice(work, format!("{block_path}.dose.work").as_str())?;

        if let Some(recovery) = dose_obj.get("recovery") {
            validate_dose_slice(recovery, format!("{block_path}.dose.recovery").as_str())?;
        }

        if let Some(repeats_value) = dose_obj.get("repeats") {
            let Some(repeats) = parse_flexible_float(repeats_value) else {
                let field = format!("{block_path}.dose.repeats");
                return Err(policy_violation(
                    "inv_session_block_repeats_invalid",
                    format!("{field} must be numeric"),
                    Some(field.as_str()),
                    Some(repeats_value.clone()),
                    Some("Use repeats >= 1."),
                ));
            };
            if repeats < 1.0 {
                let field = format!("{block_path}.dose.repeats");
                return Err(policy_violation(
                    "inv_session_block_repeats_out_of_range",
                    format!("{field} must be >= 1"),
                    Some(field.as_str()),
                    Some(repeats_value.clone()),
                    Some("Use repeats >= 1."),
                ));
            }
        }

        let intensity_status = block_obj
            .get("intensity_anchors_status")
            .and_then(Value::as_str)
            .map(|v| v.trim().to_lowercase());
        if let Some(status) = intensity_status.as_deref() {
            if status != "provided" && status != "not_applicable" {
                let field = format!("{block_path}.intensity_anchors_status");
                return Err(policy_violation(
                    "inv_session_block_anchor_status_invalid",
                    format!("{field} must be 'provided' or 'not_applicable'"),
                    Some(field.as_str()),
                    block_obj.get("intensity_anchors_status").cloned(),
                    Some("Use provided or not_applicable."),
                ));
            }
        }

        let anchors = block_obj
            .get("intensity_anchors")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        for (anchor_idx, anchor) in anchors.iter().enumerate() {
            let anchor_path = format!("{block_path}.intensity_anchors[{anchor_idx}]");
            validate_measurement_object(
                anchor,
                anchor_path.as_str(),
                "inv_session_block_anchor_measurement_state_required",
                "inv_session_block_anchor_invalid",
                "inv_session_block_anchor_value_required",
            )?;
        }

        if intensity_status.as_deref() == Some("not_applicable") && !anchors.is_empty() {
            let field = format!("{block_path}.intensity_anchors");
            return Err(policy_violation(
                "inv_session_block_anchor_not_applicable_conflict",
                format!("{field} must be empty when intensity_anchors_status=not_applicable"),
                Some(field.as_str()),
                Some(Value::Array(anchors)),
                Some("Set intensity_anchors_status='provided' if anchors are present."),
            ));
        }

        if is_performance_block_type(block_type.as_str()) {
            let has_anchors = block_obj
                .get("intensity_anchors")
                .and_then(Value::as_array)
                .is_some_and(|entries| !entries.is_empty());
            let explicitly_not_applicable = intensity_status.as_deref() == Some("not_applicable");
            if !has_anchors && !explicitly_not_applicable {
                let field = format!("{block_path}.intensity_anchors");
                return Err(policy_violation(
                    "inv_session_block_anchor_required",
                    format!(
                        "{field} requires at least one anchor for performance blocks, or set intensity_anchors_status=not_applicable"
                    ),
                    Some(field.as_str()),
                    block_obj.get("intensity_anchors").cloned(),
                    Some(
                        "Provide at least one anchor (pace/power/HR/RPE/%reference) or explicit not_applicable.",
                    ),
                ));
            }
        }

        if let Some(metrics) = block_obj.get("metrics") {
            let Some(metrics_obj) = metrics.as_object() else {
                let field = format!("{block_path}.metrics");
                return Err(policy_violation(
                    "inv_session_block_metrics_invalid",
                    format!("{field} must be an object when provided"),
                    Some(field.as_str()),
                    Some(metrics.clone()),
                    Some("Use metrics as a key/value object."),
                ));
            };
            for (metric_name, metric_value) in metrics_obj {
                let metric_path = format!("{block_path}.metrics.{metric_name}");
                validate_measurement_object(
                    metric_value,
                    metric_path.as_str(),
                    "inv_session_block_metric_measurement_state_required",
                    "inv_session_block_metric_invalid",
                    "inv_session_block_metric_value_required",
                )?;
            }
        }
    }

    Ok(())
}

fn validate_retraction_invariants(data: &serde_json::Value) -> Result<(), AppError> {
    let target_id = non_empty_string_field(data, "retracted_event_id").ok_or_else(|| {
        policy_violation(
            "inv_retraction_target_required",
            "event.retracted requires data.retracted_event_id",
            Some("data.retracted_event_id"),
            data.get("retracted_event_id").cloned(),
            Some("Provide the UUID of the event that should be retracted."),
        )
    })?;

    if Uuid::parse_str(&target_id).is_err() {
        return Err(policy_violation(
            "inv_retraction_target_invalid_uuid",
            "data.retracted_event_id must be a valid UUID",
            Some("data.retracted_event_id"),
            Some(serde_json::Value::String(target_id)),
            Some("Use the exact event.id of the event to retract."),
        ));
    }

    if data.get("retracted_event_type").is_some()
        && non_empty_string_field(data, "retracted_event_type").is_none()
    {
        return Err(policy_violation(
            "inv_retraction_type_invalid",
            "data.retracted_event_type, when provided, must be a non-empty string",
            Some("data.retracted_event_type"),
            data.get("retracted_event_type").cloned(),
            Some("Set retracted_event_type to the original event_type, for example 'set.logged'."),
        ));
    }

    Ok(())
}

fn validate_set_correction_invariants(data: &serde_json::Value) -> Result<(), AppError> {
    let target_id = non_empty_string_field(data, "target_event_id").ok_or_else(|| {
        policy_violation(
            "inv_set_correction_target_required",
            "set.corrected requires data.target_event_id",
            Some("data.target_event_id"),
            data.get("target_event_id").cloned(),
            Some("Provide the UUID of the set.logged event that should be corrected."),
        )
    })?;

    if Uuid::parse_str(&target_id).is_err() {
        return Err(policy_violation(
            "inv_set_correction_target_invalid_uuid",
            "data.target_event_id must be a valid UUID",
            Some("data.target_event_id"),
            Some(serde_json::Value::String(target_id)),
            Some("Use the exact event.id of the target set.logged event."),
        ));
    }

    let changed_fields = data.get("changed_fields").ok_or_else(|| {
        policy_violation(
            "inv_set_correction_changed_fields_required",
            "set.corrected requires data.changed_fields",
            Some("data.changed_fields"),
            None,
            Some("Provide an object with at least one field patch."),
        )
    })?;

    let changed_fields_obj = changed_fields.as_object().ok_or_else(|| {
        policy_violation(
            "inv_set_correction_changed_fields_invalid",
            "data.changed_fields must be an object",
            Some("data.changed_fields"),
            Some(changed_fields.clone()),
            Some("Use an object map, e.g. {'rest_seconds': 90}."),
        )
    })?;

    if changed_fields_obj.is_empty() {
        return Err(policy_violation(
            "inv_set_correction_changed_fields_empty",
            "data.changed_fields must not be empty",
            Some("data.changed_fields"),
            Some(changed_fields.clone()),
            Some("Include at least one changed field in set.corrected."),
        ));
    }

    if changed_fields_obj.keys().any(|k| k.trim().is_empty()) {
        return Err(policy_violation(
            "inv_set_correction_changed_fields_key_invalid",
            "data.changed_fields contains an empty field name",
            Some("data.changed_fields"),
            Some(changed_fields.clone()),
            Some("Each changed_fields key must be a non-empty field name."),
        ));
    }

    Ok(())
}

fn validate_projection_rule_created_invariants(data: &serde_json::Value) -> Result<(), AppError> {
    let name = non_empty_string_field(data, "name").ok_or_else(|| {
        policy_violation(
            "inv_projection_rule_name_required",
            "projection_rule.created requires data.name",
            Some("data.name"),
            data.get("name").cloned(),
            Some("Provide a stable non-empty rule name."),
        )
    })?;

    let rule_type = non_empty_string_field(data, "rule_type").ok_or_else(|| {
        policy_violation(
            "inv_projection_rule_type_required",
            "projection_rule.created requires data.rule_type",
            Some("data.rule_type"),
            data.get("rule_type").cloned(),
            Some("Use one of: field_tracking, categorized_tracking."),
        )
    })?;

    if !matches!(
        rule_type.as_str(),
        "field_tracking" | "categorized_tracking"
    ) {
        return Err(policy_violation(
            "inv_projection_rule_type_invalid",
            format!(
                "projection_rule.created has unsupported rule_type '{}'",
                rule_type
            ),
            Some("data.rule_type"),
            Some(serde_json::Value::String(rule_type)),
            Some("Allowed values: field_tracking, categorized_tracking."),
        ));
    }

    let source_events = non_empty_string_array(data, "source_events").ok_or_else(|| {
        policy_violation(
            "inv_projection_rule_source_events_invalid",
            format!(
                "projection_rule.created '{}' requires non-empty data.source_events",
                name
            ),
            Some("data.source_events"),
            data.get("source_events").cloned(),
            Some("Provide at least one non-empty source event type."),
        )
    })?;

    let fields = non_empty_string_array(data, "fields").ok_or_else(|| {
        policy_violation(
            "inv_projection_rule_fields_invalid",
            format!(
                "projection_rule.created '{}' requires non-empty data.fields",
                name
            ),
            Some("data.fields"),
            data.get("fields").cloned(),
            Some("Provide at least one non-empty field name."),
        )
    })?;

    if source_events.len() > 32 {
        return Err(policy_violation(
            "inv_projection_rule_source_events_too_large",
            "data.source_events exceeds maximum length of 32",
            Some("data.source_events"),
            Some(serde_json::json!(source_events.len())),
            Some("Split very broad rules into smaller focused projection rules."),
        ));
    }

    if fields.len() > 64 {
        return Err(policy_violation(
            "inv_projection_rule_fields_too_large",
            "data.fields exceeds maximum length of 64",
            Some("data.fields"),
            Some(serde_json::json!(fields.len())),
            Some("Reduce tracked fields per rule to keep processing bounded."),
        ));
    }

    if rule_type == "categorized_tracking" {
        let group_by = non_empty_string_field(data, "group_by").ok_or_else(|| {
            policy_violation(
                "inv_projection_rule_group_by_required",
                "categorized_tracking requires data.group_by",
                Some("data.group_by"),
                data.get("group_by").cloned(),
                Some("Set group_by to one of the declared fields."),
            )
        })?;

        if !fields.iter().any(|field| field == &group_by) {
            return Err(policy_violation(
                "inv_projection_rule_group_by_not_in_fields",
                format!(
                    "data.group_by '{}' must be included in data.fields",
                    group_by
                ),
                Some("data.group_by"),
                Some(serde_json::Value::String(group_by)),
                Some("Add group_by to data.fields or choose an existing field."),
            ));
        }
    }

    Ok(())
}

fn validate_projection_rule_archived_invariants(data: &serde_json::Value) -> Result<(), AppError> {
    if non_empty_string_field(data, "name").is_none() {
        return Err(policy_violation(
            "inv_projection_rule_archive_name_required",
            "projection_rule.archived requires data.name",
            Some("data.name"),
            data.get("name").cloned(),
            Some("Provide the exact rule name to archive."),
        ));
    }

    Ok(())
}

const WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE: &str = "workflow.onboarding.closed";
const WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE: &str = "workflow.onboarding.override_granted";
const LEGACY_PLANNING_OR_COACHING_EVENT_TYPES: [&str; 8] = [
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    "projection_rule.created",
    "projection_rule.archived",
    "weight_target.set",
    "sleep_target.set",
    "nutrition_target.set",
];
const LEGACY_PLAN_EVENT_TYPES: [&str; 3] = [
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
];
const LEGACY_TIMEZONE_REQUIRED_EVENT_TYPES: [&str; 12] = [
    "set.logged",
    "session.logged",
    "session.completed",
    "bodyweight.logged",
    "measurement.logged",
    "sleep.logged",
    "energy.logged",
    "soreness.logged",
    "recovery.daily_checkin",
    "meal.logged",
    "observation.logged",
    "external.activity_imported",
];

fn normalize_event_type(event_type: &str) -> String {
    event_type.trim().to_lowercase()
}

fn is_planning_or_coaching_event_type(event_type: &str) -> bool {
    LEGACY_PLANNING_OR_COACHING_EVENT_TYPES.contains(&event_type)
}

fn is_plan_event_type(event_type: &str) -> bool {
    LEGACY_PLAN_EVENT_TYPES.contains(&event_type)
}

fn is_timezone_required_event_type(event_type: &str) -> bool {
    LEGACY_TIMEZONE_REQUIRED_EVENT_TYPES.contains(&event_type)
}

fn event_carries_timezone_context(event: &CreateEventRequest) -> bool {
    for key in ["timezone", "time_zone"] {
        let value = event
            .data
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or_default();
        if !value.is_empty() {
            return true;
        }
    }
    if let Some(session_meta) = event.data.get("session_meta").and_then(Value::as_object) {
        for key in ["timezone", "time_zone"] {
            let value = session_meta
                .get(key)
                .and_then(Value::as_str)
                .map(str::trim)
                .unwrap_or_default();
            if !value.is_empty() {
                return true;
            }
        }
    }
    false
}

fn has_timezone_preference_in_user_profile(profile: &Value) -> bool {
    let Some(user) = profile.get("user").and_then(Value::as_object) else {
        return false;
    };
    let Some(preferences) = user.get("preferences").and_then(Value::as_object) else {
        return false;
    };
    for key in ["timezone", "time_zone"] {
        let value = preferences
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or_default();
        if !value.is_empty() {
            return true;
        }
    }
    false
}

fn onboarding_closed_in_user_profile(profile: &Value) -> bool {
    profile
        .get("user")
        .and_then(|user| user.get("workflow_state"))
        .and_then(|workflow| workflow.get("onboarding_closed"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn onboarding_override_active_in_user_profile(profile: &Value) -> bool {
    profile
        .get("user")
        .and_then(|user| user.get("workflow_state"))
        .and_then(|workflow| workflow.get("override_active"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn evaluate_legacy_domain_invariants(
    events: &[CreateEventRequest],
    user_profile: Option<&Value>,
) -> Result<(), AppError> {
    if events.is_empty() {
        return Ok(());
    }

    let normalized_event_types: Vec<String> = events
        .iter()
        .map(|event| normalize_event_type(&event.event_type))
        .collect();
    let planning_event_types: Vec<String> = normalized_event_types
        .iter()
        .filter(|event_type| is_planning_or_coaching_event_type(event_type))
        .cloned()
        .collect();
    let has_plan_writes = normalized_event_types
        .iter()
        .any(|event_type| is_plan_event_type(event_type));

    let requested_close = normalized_event_types
        .iter()
        .any(|event_type| event_type == WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE);
    let requested_override = normalized_event_types
        .iter()
        .any(|event_type| event_type == WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE);
    let onboarding_closed = user_profile
        .map(onboarding_closed_in_user_profile)
        .unwrap_or(false);
    let override_active = user_profile
        .map(onboarding_override_active_in_user_profile)
        .unwrap_or(false);

    if !planning_event_types.is_empty()
        && !(onboarding_closed || override_active || requested_close || requested_override)
    {
        return Err(policy_violation(
            "inv_workflow_phase_required",
            "Planning/coaching writes require onboarding close or explicit override",
            Some("events"),
            Some(serde_json::json!({
                "planning_event_types": planning_event_types,
                "onboarding_closed": onboarding_closed,
                "override_active": override_active,
            })),
            Some(
                "Emit workflow.onboarding.closed (or workflow.onboarding.override_granted) before planning/coaching writes.",
            ),
        ));
    }

    if has_plan_writes {
        tracing::warn!(
            event_types = ?normalized_event_types,
            violation_code = "inv_plan_write_requires_write_with_proof",
            "legacy_plan_write_blocked"
        );
        return Err(policy_violation(
            "inv_plan_write_requires_write_with_proof",
            "training_plan.* writes require /v1/agent/write-with-proof to satisfy read-after-write guarantees",
            Some("events"),
            Some(serde_json::json!({"event_types": normalized_event_types})),
            Some(
                "Route plan writes through POST /v1/agent/write-with-proof with read_after_write_targets.",
            ),
        ));
    }

    let timezone_missing = !user_profile
        .map(has_timezone_preference_in_user_profile)
        .unwrap_or(false);
    if timezone_missing {
        let missing_timezone_for_event_types: Vec<String> = events
            .iter()
            .filter_map(|event| {
                let normalized = normalize_event_type(&event.event_type);
                if !is_timezone_required_event_type(&normalized) {
                    return None;
                }
                if event_carries_timezone_context(event) {
                    return None;
                }
                Some(normalized)
            })
            .collect();

        if !missing_timezone_for_event_types.is_empty() {
            return Err(policy_violation(
                "inv_timezone_required_for_temporal_write",
                "Timezone preference is required before temporal writes",
                Some("events"),
                Some(serde_json::json!({
                    "event_types": missing_timezone_for_event_types,
                })),
                Some(
                    "Persist preference.set {\"key\":\"timezone\"} first, or include data.timezone/time_zone explicitly.",
                ),
            ));
        }
    }

    Ok(())
}

async fn fetch_user_profile_projection_data(
    pool: &sqlx::PgPool,
    user_id: Uuid,
) -> Result<Option<Value>, AppError> {
    let mut tx = pool.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let row = sqlx::query_as::<_, (Value,)>(
        r#"
        SELECT data
        FROM projections
        WHERE user_id = $1
          AND projection_type = 'user_profile'
          AND key = 'me'
        LIMIT 1
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut *tx)
    .await?;

    tx.commit().await?;
    Ok(row.map(|(data,)| data))
}

pub(crate) async fn enforce_legacy_domain_invariants(
    state: &AppState,
    user_id: Uuid,
    events: &[CreateEventRequest],
) -> Result<(), AppError> {
    let profile = fetch_user_profile_projection_data(&state.db, user_id).await?;
    evaluate_legacy_domain_invariants(events, profile.as_ref())
}

/// Check event data for plausibility and return warnings.
/// These are soft checks — events are always accepted.
fn check_event_plausibility(event_type: &str, data: &serde_json::Value) -> Vec<EventWarning> {
    let mut warnings = Vec::new();

    match event_type {
        "set.logged" => {
            if let Some(w) = data.get("weight_kg").and_then(|v| v.as_f64()) {
                if w < 0.0 || w > 500.0 {
                    warnings.push(EventWarning {
                        field: "weight_kg".to_string(),
                        message: format!("weight_kg={w} outside plausible range [0, 500]"),
                        severity: "warning".to_string(),
                    });
                }
            }
            if let Some(r) = data.get("reps").and_then(|v| v.as_i64()) {
                if r < 0 || r > 100 {
                    warnings.push(EventWarning {
                        field: "reps".to_string(),
                        message: format!("reps={r} outside plausible range [0, 100]"),
                        severity: "warning".to_string(),
                    });
                }
            }
            let rpe = data.get("rpe").and_then(parse_flexible_float);
            if let Some(value) = rpe {
                if !(1.0..=10.0).contains(&value) {
                    warnings.push(EventWarning {
                        field: "rpe".to_string(),
                        message: format!("rpe={value} outside plausible range [1, 10]"),
                        severity: "warning".to_string(),
                    });
                }
            }
            let rir = data.get("rir").and_then(parse_flexible_float);
            if let Some(value) = rir {
                if !(0.0..=10.0).contains(&value) {
                    warnings.push(EventWarning {
                        field: "rir".to_string(),
                        message: format!("rir={value} outside plausible range [0, 10]"),
                        severity: "warning".to_string(),
                    });
                }
            }
            if let (Some(rpe), Some(rir)) = (rpe, rir) {
                let delta = (rpe + rir - 10.0).abs();
                if delta > 2.0 {
                    warnings.push(EventWarning {
                        field: "rpe_rir".to_string(),
                        message: format!(
                            "rpe+rir consistency warning: rpe={rpe}, rir={rir}, expected sum near 10"
                        ),
                        severity: "warning".to_string(),
                    });
                }
            }
        }
        "bodyweight.logged" => {
            if let Some(w) = data.get("weight_kg").and_then(|v| v.as_f64()) {
                if w < 20.0 || w > 300.0 {
                    warnings.push(EventWarning {
                        field: "weight_kg".to_string(),
                        message: format!("weight_kg={w} outside plausible range [20, 300]"),
                        severity: "warning".to_string(),
                    });
                }
            }
        }
        "meal.logged" => {
            if let Some(c) = data.get("calories").and_then(|v| v.as_f64()) {
                if c < 0.0 || c > 5000.0 {
                    warnings.push(EventWarning {
                        field: "calories".to_string(),
                        message: format!("calories={c} outside plausible range [0, 5000]"),
                        severity: "warning".to_string(),
                    });
                }
            }
            for macro_field in &["protein_g", "carbs_g", "fat_g"] {
                if let Some(v) = data.get(*macro_field).and_then(|v| v.as_f64()) {
                    if v < 0.0 || v > 500.0 {
                        warnings.push(EventWarning {
                            field: macro_field.to_string(),
                            message: format!("{macro_field}={v} outside plausible range [0, 500]"),
                            severity: "warning".to_string(),
                        });
                    }
                }
            }
        }
        "sleep.logged" => {
            if let Some(d) = data.get("duration_hours").and_then(|v| v.as_f64()) {
                if d < 0.0 || d > 20.0 {
                    warnings.push(EventWarning {
                        field: "duration_hours".to_string(),
                        message: format!("duration_hours={d} outside plausible range [0, 20]"),
                        severity: "warning".to_string(),
                    });
                }
            }
        }
        "soreness.logged" => {
            if let Some(s) = data.get("severity").and_then(|v| v.as_f64()) {
                if s < 0.0 || s > 10.0 {
                    warnings.push(EventWarning {
                        field: "severity".to_string(),
                        message: format!("severity={s} outside plausible range [0, 10]"),
                        severity: "warning".to_string(),
                    });
                }
            }
        }
        "energy.logged" => {
            if let Some(l) = data.get("level").and_then(|v| v.as_f64()) {
                if l < 1.0 || l > 10.0 {
                    warnings.push(EventWarning {
                        field: "level".to_string(),
                        message: format!("level={l} outside plausible range [1, 10]"),
                        severity: "warning".to_string(),
                    });
                }
            }
        }
        "recovery.daily_checkin" => {
            if let Some(w) = data.get("bodyweight_kg").and_then(parse_flexible_float) {
                if w < 20.0 || w > 300.0 {
                    warnings.push(EventWarning {
                        field: "bodyweight_kg".to_string(),
                        message: format!("bodyweight_kg={w} outside plausible range [20, 300]"),
                        severity: "warning".to_string(),
                    });
                }
            }
            if let Some(sleep) = data.get("sleep_hours").and_then(parse_flexible_float) {
                if !(0.0..=20.0).contains(&sleep) {
                    warnings.push(EventWarning {
                        field: "sleep_hours".to_string(),
                        message: format!("sleep_hours={sleep} outside plausible range [0, 20]"),
                        severity: "warning".to_string(),
                    });
                }
            }
            for (field, min, max) in [
                ("soreness", 0.0_f64, 10.0_f64),
                ("motivation", 1.0_f64, 10.0_f64),
                ("sleep_quality", 1.0_f64, 10.0_f64),
                ("physical_condition", 1.0_f64, 10.0_f64),
                ("lifestyle_stability", 1.0_f64, 10.0_f64),
            ] {
                if let Some(value) = data.get(field).and_then(parse_flexible_float) {
                    if value < min || value > max {
                        warnings.push(EventWarning {
                            field: field.to_string(),
                            message: format!(
                                "{}={} outside plausible range [{}, {}]",
                                field, value, min, max
                            ),
                            severity: "warning".to_string(),
                        });
                    }
                }
            }
            if let Some(hrv) = data.get("hrv_rmssd").and_then(parse_flexible_float) {
                if hrv <= 0.0 || hrv > 400.0 {
                    warnings.push(EventWarning {
                        field: "hrv_rmssd".to_string(),
                        message: format!("hrv_rmssd={hrv} outside plausible range (0, 400]"),
                        severity: "warning".to_string(),
                    });
                }
            }
            if let Some(raw) = data.get("alcohol_last_night").and_then(Value::as_str) {
                let normalized = raw.trim().to_lowercase();
                if !normalized.is_empty()
                    && !matches!(normalized.as_str(), "none" | "little" | "too_much")
                {
                    warnings.push(EventWarning {
                        field: "alcohol_last_night".to_string(),
                        message: format!(
                            "alcohol_last_night='{}' is non-standard, expected none|little|too_much",
                            normalized
                        ),
                        severity: "warning".to_string(),
                    });
                }
            }
            if let Some(raw) = data.get("training_yesterday").and_then(Value::as_str) {
                let normalized = raw.trim().to_lowercase();
                if !normalized.is_empty()
                    && !matches!(normalized.as_str(), "rest" | "easy" | "average" | "hard")
                {
                    warnings.push(EventWarning {
                        field: "training_yesterday".to_string(),
                        message: format!(
                            "training_yesterday='{}' is non-standard, expected rest|easy|average|hard",
                            normalized
                        ),
                        severity: "warning".to_string(),
                    });
                }
            }
        }
        "measurement.logged" => {
            if let Some(v) = data.get("value_cm").and_then(|v| v.as_f64()) {
                if v < 1.0 || v > 300.0 {
                    warnings.push(EventWarning {
                        field: "value_cm".to_string(),
                        message: format!("value_cm={v} outside plausible range [1, 300]"),
                        severity: "warning".to_string(),
                    });
                }
            }
        }
        "session.completed" => {
            for field in &["enjoyment", "perceived_quality", "perceived_exertion"] {
                if let Some(v) = data.get(*field).and_then(parse_flexible_float) {
                    if !(1.0..=10.0).contains(&v) {
                        warnings.push(EventWarning {
                            field: field.to_string(),
                            message: format!("{field}={v} outside plausible range [1, 10]"),
                            severity: "warning".to_string(),
                        });
                    }
                }
            }
            if let Some(v) = data.get("pain_discomfort").and_then(parse_flexible_float) {
                if !(0.0..=10.0).contains(&v) {
                    warnings.push(EventWarning {
                        field: "pain_discomfort".to_string(),
                        message: format!("pain_discomfort={v} outside plausible range [0, 10]"),
                        severity: "warning".to_string(),
                    });
                }
            }
        }
        "training_plan.created" | "training_plan.updated" => {
            if data
                .get("name")
                .and_then(Value::as_str)
                .map(str::trim)
                .is_none_or(|name| name.is_empty())
            {
                warnings.push(EventWarning {
                    field: "name".to_string(),
                    message: "training plan name missing; a deterministic fallback name will be used".to_string(),
                    severity: "warning".to_string(),
                });
            }
        }
        _ => {} // Unknown event types: no plausibility checks
    }

    warnings
}

/// Fetch all distinct exercise_ids for a user from the events table.
async fn fetch_user_exercise_ids(
    pool: &sqlx::PgPool,
    user_id: Uuid,
) -> Result<HashSet<String>, AppError> {
    let mut tx = pool.begin().await?;

    // Set RLS context so this read is guaranteed to stay user-scoped.
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let rows = sqlx::query_scalar::<_, String>(
        r#"
        SELECT DISTINCT lower(trim(data->>'exercise_id'))
        FROM events
        WHERE user_id = $1
          AND data->>'exercise_id' IS NOT NULL
          AND trim(data->>'exercise_id') != ''
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut *tx)
    .await?;

    tx.commit().await?;

    Ok(rows.into_iter().collect())
}

/// Check if an exercise_id is new and similar to existing ones.
/// Returns a warning if close matches are found (Jaro-Winkler >= 0.8).
fn check_exercise_id_similarity(
    event_type: &str,
    data: &serde_json::Value,
    known_ids: &HashSet<String>,
) -> Vec<EventWarning> {
    // Only check relevant event types
    if event_type != "set.logged" && event_type != "exercise.alias_created" {
        return Vec::new();
    }

    let exercise_id = match data.get("exercise_id").and_then(|v| v.as_str()) {
        Some(id) if !id.trim().is_empty() => id.trim().to_lowercase(),
        _ => return Vec::new(),
    };

    // If already known, no warning needed
    if known_ids.contains(&exercise_id) {
        return Vec::new();
    }

    // Find similar existing exercise_ids
    let mut similar: Vec<&String> = known_ids
        .iter()
        .filter(|existing| strsim::jaro_winkler(&exercise_id, existing) >= 0.8)
        .collect();

    if similar.is_empty() {
        return Vec::new();
    }

    similar.sort();
    let similar_str: Vec<&str> = similar.iter().map(|s| s.as_str()).collect();
    vec![EventWarning {
        field: "exercise_id".to_string(),
        message: format!(
            "New exercise_id '{}'. Similar existing: {}",
            exercise_id,
            similar_str.join(", ")
        ),
        severity: "warning".to_string(),
    }]
}

/// Insert a single event into the database within a transaction that sets RLS context.
async fn insert_event(
    pool: &sqlx::PgPool,
    user_id: Uuid,
    req: CreateEventRequest,
) -> Result<Event, AppError> {
    let event_id = Uuid::now_v7();
    let metadata_json = serde_json::to_value(&req.metadata)
        .map_err(|e| AppError::Internal(format!("Failed to serialize metadata: {}", e)))?;

    let mut tx = pool.begin().await?;

    // Set RLS context: this transaction can only see/write events for this user
    // Uses set_config with parameter binding (not format!) to prevent SQL injection
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let row = sqlx::query_as::<_, EventRow>(
        r#"
        INSERT INTO events (id, user_id, timestamp, event_type, data, metadata)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, user_id, timestamp, event_type, data, metadata, created_at
        "#,
    )
    .bind(event_id)
    .bind(user_id)
    .bind(req.timestamp)
    .bind(&req.event_type)
    .bind(&req.data)
    .bind(&metadata_json)
    .fetch_one(&mut *tx)
    .await
    .map_err(|e| {
        // Check for idempotency conflict specifically
        if let sqlx::Error::Database(ref db_err) = e {
            if db_err.code().as_deref() == Some("23505") {
                return AppError::IdempotencyConflict {
                    idempotency_key: req.metadata.idempotency_key.clone(),
                };
            }
        }
        AppError::Database(e)
    })?;

    tx.commit().await?;

    Ok(row.into_event())
}

#[derive(Debug, Clone, Eq, PartialEq, Hash)]
struct ProjectionTargetKey {
    projection_type: String,
    key: String,
}

#[derive(Debug, Default, Clone)]
struct ProjectionTargetCandidate {
    reasons: Vec<String>,
    delete_hint: bool,
    unknown_target: bool,
}

#[derive(sqlx::FromRow)]
struct ExistingProjectionVersionRow {
    projection_type: String,
    key: String,
    version: i64,
}

#[derive(sqlx::FromRow)]
struct RuleEventSimulationRow {
    id: Uuid,
    event_type: String,
    data: serde_json::Value,
}

#[derive(sqlx::FromRow)]
struct ResolvedEventRow {
    event_type: String,
    data: serde_json::Value,
}

fn add_projection_target(
    candidates: &mut HashMap<ProjectionTargetKey, ProjectionTargetCandidate>,
    projection_type: &str,
    key: &str,
    reason: String,
    delete_hint: bool,
    unknown_target: bool,
) {
    let entry = candidates
        .entry(ProjectionTargetKey {
            projection_type: projection_type.to_string(),
            key: key.to_string(),
        })
        .or_default();

    if !entry.reasons.iter().any(|r| r == &reason) {
        entry.reasons.push(reason);
    }
    if delete_hint {
        entry.delete_hint = true;
    }
    if unknown_target {
        entry.unknown_target = true;
    }
}

fn normalize_fallback_exercise_key(raw: &str) -> String {
    raw.trim().to_lowercase().replace(' ', "_")
}

fn extract_exercise_key(data: &serde_json::Value) -> Option<String> {
    if let Some(exercise_id) = data.get("exercise_id").and_then(|v| v.as_str()) {
        let key = exercise_id.trim().to_lowercase();
        if !key.is_empty() {
            return Some(key);
        }
    }

    if let Some(exercise) = data.get("exercise").and_then(|v| v.as_str()) {
        let key = normalize_fallback_exercise_key(exercise);
        if !key.is_empty() {
            return Some(key);
        }
    }

    None
}

fn extract_observation_dimension(data: &serde_json::Value) -> Option<String> {
    let raw = data.get("dimension").and_then(|v| v.as_str())?;
    let normalized = raw.trim().to_lowercase().replace(' ', "_");
    if normalized.is_empty() {
        None
    } else {
        Some(normalized)
    }
}

fn user_profile_handles_event(event_type: &str) -> bool {
    matches!(
        event_type,
        "set.logged"
            | "session.logged"
            | "set.corrected"
            | "exercise.alias_created"
            | "preference.set"
            | "goal.set"
            | "profile.updated"
            | "program.started"
            | "injury.reported"
            | "bodyweight.logged"
            | "measurement.logged"
            | "sleep.logged"
            | "soreness.logged"
            | "energy.logged"
            | "recovery.daily_checkin"
            | "meal.logged"
            | "training_plan.created"
            | "training_plan.updated"
            | "training_plan.archived"
            | "nutrition_target.set"
            | "sleep_target.set"
            | "weight_target.set"
            | "session.completed"
    )
}

fn add_standard_projection_targets(
    candidates: &mut HashMap<ProjectionTargetKey, ProjectionTargetCandidate>,
    event_type: &str,
    data: &serde_json::Value,
) -> bool {
    let mut mapped = false;

    if user_profile_handles_event(event_type) {
        mapped = true;
        add_projection_target(
            candidates,
            "user_profile",
            "me",
            format!(
                "event_type '{}' triggers user_profile recompute",
                event_type
            ),
            false,
            false,
        );
    }

    match event_type {
        "set.logged" => {
            mapped = true;
            add_projection_target(
                candidates,
                "training_timeline",
                "overview",
                "set.logged updates training timeline aggregates".to_string(),
                false,
                false,
            );

            if let Some(exercise_key) = extract_exercise_key(data) {
                add_projection_target(
                    candidates,
                    "exercise_progression",
                    &exercise_key,
                    "set.logged updates per-exercise progression".to_string(),
                    false,
                    false,
                );
            } else {
                add_projection_target(
                    candidates,
                    "exercise_progression",
                    "*",
                    "set.logged without exercise_id/exercise cannot map to a concrete exercise key"
                        .to_string(),
                    false,
                    true,
                );
            }

            add_projection_target(
                candidates,
                "readiness_inference",
                "overview",
                "set.logged contributes training load signal for readiness inference".to_string(),
                false,
                false,
            );
            add_projection_target(
                candidates,
                "causal_inference",
                "overview",
                "set.logged contributes intervention context for causal inference".to_string(),
                false,
                false,
            );

            add_projection_target(
                candidates,
                "semantic_memory",
                "overview",
                "set.logged contributes user exercise vocabulary for semantic indexing".to_string(),
                false,
                false,
            );

            if let Some(exercise_key) = extract_exercise_key(data) {
                add_projection_target(
                    candidates,
                    "strength_inference",
                    &exercise_key,
                    "set.logged updates Bayesian strength inference per exercise".to_string(),
                    false,
                    false,
                );
            } else {
                add_projection_target(
                    candidates,
                    "strength_inference",
                    "*",
                    "set.logged without exercise identifier cannot map to strength_inference key"
                        .to_string(),
                    false,
                    true,
                );
            }
        }
        "session.logged" => {
            mapped = true;
            add_projection_target(
                candidates,
                "training_timeline",
                "overview",
                "session.logged updates modality-neutral timeline load".to_string(),
                false,
                false,
            );
            add_projection_target(
                candidates,
                "session_feedback",
                "overview",
                "session.logged contributes session-load context for feedback alignment"
                    .to_string(),
                false,
                false,
            );
        }
        "set.corrected" => {
            mapped = true;
            add_projection_target(
                candidates,
                "training_timeline",
                "overview",
                "set.corrected can update effective set load in training timeline".to_string(),
                false,
                false,
            );
            add_projection_target(
                candidates,
                "exercise_progression",
                "*",
                "set.corrected can update per-exercise progression via corrected set values"
                    .to_string(),
                false,
                true,
            );
            add_projection_target(
                candidates,
                "session_feedback",
                "overview",
                "set.corrected can update load-to-feedback alignment".to_string(),
                false,
                false,
            );
        }
        "exercise.alias_created" => {
            mapped = true;
            add_projection_target(
                candidates,
                "training_timeline",
                "overview",
                "exercise.alias_created can remap historical exercise keys in timeline".to_string(),
                false,
                false,
            );

            if let Some(exercise_key) = extract_exercise_key(data) {
                add_projection_target(
                    candidates,
                    "exercise_progression",
                    &exercise_key,
                    "exercise.alias_created can trigger exercise progression consolidation"
                        .to_string(),
                    false,
                    false,
                );
            } else {
                add_projection_target(
                    candidates,
                    "exercise_progression",
                    "*",
                    "exercise.alias_created without exercise_id cannot map to a concrete exercise key"
                        .to_string(),
                    false,
                    true,
                );
            }

            add_projection_target(
                candidates,
                "semantic_memory",
                "overview",
                "exercise.alias_created contributes semantic alias memory".to_string(),
                false,
                false,
            );

            if let Some(exercise_key) = extract_exercise_key(data) {
                add_projection_target(
                    candidates,
                    "strength_inference",
                    &exercise_key,
                    "exercise.alias_created can remap Bayesian strength inference keys".to_string(),
                    false,
                    false,
                );
            } else {
                add_projection_target(
                    candidates,
                    "strength_inference",
                    "*",
                    "exercise.alias_created without exercise_id cannot map strength_inference key"
                        .to_string(),
                    false,
                    true,
                );
            }
        }
        "session.completed" => {
            mapped = true;
            add_projection_target(
                candidates,
                "session_feedback",
                "overview",
                "session.completed updates subjective session feedback trends".to_string(),
                false,
                false,
            );
        }
        "recovery.daily_checkin" => {
            mapped = true;
            add_projection_target(
                candidates,
                "recovery",
                "overview",
                "recovery.daily_checkin updates recovery overview".to_string(),
                false,
                false,
            );
            add_projection_target(
                candidates,
                "body_composition",
                "overview",
                "recovery.daily_checkin can contribute bodyweight snapshot".to_string(),
                false,
                false,
            );
            add_projection_target(
                candidates,
                "readiness_inference",
                "overview",
                "recovery.daily_checkin contributes readiness inference signals".to_string(),
                false,
                false,
            );
            add_projection_target(
                candidates,
                "causal_inference",
                "overview",
                "recovery.daily_checkin contributes causal inference signals".to_string(),
                false,
                false,
            );
        }
        "observation.logged" => {
            mapped = true;
            if let Some(dimension) = extract_observation_dimension(data) {
                add_projection_target(
                    candidates,
                    "open_observations",
                    &dimension,
                    "observation.logged updates open observation projection for the given dimension"
                        .to_string(),
                    false,
                    false,
                );
            } else {
                add_projection_target(
                    candidates,
                    "open_observations",
                    "*",
                    "observation.logged without dimension cannot map to a concrete open_observations key"
                        .to_string(),
                    false,
                    true,
                );
            }
        }
        "bodyweight.logged" | "measurement.logged" | "weight_target.set" => {
            mapped = true;
            add_projection_target(
                candidates,
                "body_composition",
                "overview",
                format!("event_type '{}' updates body composition", event_type),
                false,
                false,
            );
        }
        "sleep.logged" | "soreness.logged" | "energy.logged" | "sleep_target.set" => {
            mapped = true;
            add_projection_target(
                candidates,
                "recovery",
                "overview",
                format!("event_type '{}' updates recovery", event_type),
                false,
                false,
            );

            add_projection_target(
                candidates,
                "readiness_inference",
                "overview",
                format!(
                    "event_type '{}' contributes readiness inference signals",
                    event_type
                ),
                false,
                false,
            );
            add_projection_target(
                candidates,
                "causal_inference",
                "overview",
                format!(
                    "event_type '{}' contributes causal inference signals",
                    event_type
                ),
                false,
                false,
            );
        }
        "meal.logged" | "nutrition_target.set" => {
            mapped = true;
            add_projection_target(
                candidates,
                "nutrition",
                "overview",
                format!("event_type '{}' updates nutrition", event_type),
                false,
                false,
            );

            if event_type == "meal.logged" {
                add_projection_target(
                    candidates,
                    "semantic_memory",
                    "overview",
                    "meal.logged contributes food vocabulary for semantic indexing".to_string(),
                    false,
                    false,
                );
            }
            add_projection_target(
                candidates,
                "causal_inference",
                "overview",
                format!(
                    "event_type '{}' contributes causal nutrition effects",
                    event_type
                ),
                false,
                false,
            );
        }
        "training_plan.created"
        | "training_plan.updated"
        | "training_plan.archived"
        | "program.started" => {
            mapped = true;
            add_projection_target(
                candidates,
                "training_plan",
                "overview",
                format!("event_type '{}' updates training plan state", event_type),
                false,
                false,
            );
            add_projection_target(
                candidates,
                "causal_inference",
                "overview",
                format!(
                    "event_type '{}' marks causal program intervention timing",
                    event_type
                ),
                false,
                false,
            );
        }
        "projection_rule.created" => {
            mapped = true;
            if let Some(rule_name) = data.get("name").and_then(|v| v.as_str()) {
                let key = rule_name.trim();
                if !key.is_empty() {
                    add_projection_target(
                        candidates,
                        "custom",
                        key,
                        "projection_rule.created creates or updates custom projection".to_string(),
                        false,
                        false,
                    );
                } else {
                    add_projection_target(
                        candidates,
                        "custom",
                        "*",
                        "projection_rule.created has empty name; custom key cannot be determined"
                            .to_string(),
                        false,
                        true,
                    );
                }
            } else {
                add_projection_target(
                    candidates,
                    "custom",
                    "*",
                    "projection_rule.created without name; custom key cannot be determined"
                        .to_string(),
                    false,
                    true,
                );
            }
        }
        "projection_rule.archived" => {
            mapped = true;
            if let Some(rule_name) = data.get("name").and_then(|v| v.as_str()) {
                let key = rule_name.trim();
                if !key.is_empty() {
                    add_projection_target(
                        candidates,
                        "custom",
                        key,
                        "projection_rule.archived deletes custom projection".to_string(),
                        true,
                        false,
                    );
                } else {
                    add_projection_target(
                        candidates,
                        "custom",
                        "*",
                        "projection_rule.archived has empty name; custom key cannot be determined"
                            .to_string(),
                        true,
                        true,
                    );
                }
            } else {
                add_projection_target(
                    candidates,
                    "custom",
                    "*",
                    "projection_rule.archived without name; custom key cannot be determined"
                        .to_string(),
                    true,
                    true,
                );
            }
        }
        _ => {}
    }

    mapped
}

fn add_custom_rule_targets(
    candidates: &mut HashMap<ProjectionTargetKey, ProjectionTargetCandidate>,
    active_custom_rules: &HashMap<String, HashSet<String>>,
    event_type: &str,
) -> bool {
    let mut mapped = false;

    for (rule_name, source_events) in active_custom_rules {
        if !source_events.contains(event_type) {
            continue;
        }

        mapped = true;
        add_projection_target(
            candidates,
            "custom",
            rule_name,
            format!(
                "active custom rule '{}' matches event_type '{}'",
                rule_name, event_type
            ),
            false,
            false,
        );
    }

    mapped
}

async fn fetch_retracted_event_ids_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
) -> Result<HashSet<Uuid>, AppError> {
    let ids: Vec<String> = sqlx::query_scalar(
        r#"
        SELECT data->>'retracted_event_id'
        FROM events
        WHERE user_id = $1
          AND event_type = 'event.retracted'
          AND data->>'retracted_event_id' IS NOT NULL
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **tx)
    .await?;

    Ok(ids
        .into_iter()
        .filter_map(|id| Uuid::parse_str(&id).ok())
        .collect())
}

async fn load_active_custom_rules(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
) -> Result<HashMap<String, HashSet<String>>, AppError> {
    let retracted_ids = fetch_retracted_event_ids_tx(tx, user_id).await?;

    let rows = sqlx::query_as::<_, RuleEventSimulationRow>(
        r#"
        SELECT id, event_type, data
        FROM events
        WHERE user_id = $1
          AND event_type IN ('projection_rule.created', 'projection_rule.archived')
        ORDER BY timestamp ASC, id ASC
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **tx)
    .await?;

    let mut active: HashMap<String, HashSet<String>> = HashMap::new();

    for row in rows {
        if retracted_ids.contains(&row.id) {
            continue;
        }

        let name = row
            .data
            .get("name")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        let Some(name) = name else {
            continue;
        };

        if row.event_type == "projection_rule.archived" {
            active.remove(&name);
            continue;
        }

        let source_events = row
            .data
            .get("source_events")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str())
                    .map(|s| s.trim().to_lowercase())
                    .filter(|s| !s.is_empty())
                    .collect::<HashSet<String>>()
            })
            .unwrap_or_default();

        active.insert(name, source_events);
    }

    Ok(active)
}

async fn resolve_retracted_event_for_simulation(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
    data: &serde_json::Value,
) -> Result<(String, serde_json::Value, Option<String>), AppError> {
    let fallback_type = data
        .get("retracted_event_type")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);

    let Some(retracted_event_id) = data
        .get("retracted_event_id")
        .and_then(|v| v.as_str())
        .and_then(|s| Uuid::parse_str(s).ok())
    else {
        if let Some(event_type) = fallback_type {
            return Ok((
                event_type.clone(),
                serde_json::json!({}),
                Some(format!(
                    "event.retracted fallback: used retracted_event_type='{}' (no retracted_event_id lookup).",
                    event_type
                )),
            ));
        }

        return Ok((
            "unknown".to_string(),
            serde_json::json!({}),
            Some(
                "event.retracted could not be resolved (missing retracted_event_id and retracted_event_type)."
                    .to_string(),
            ),
        ));
    };

    let resolved = sqlx::query_as::<_, ResolvedEventRow>(
        r#"
        SELECT event_type, data
        FROM events
        WHERE id = $1 AND user_id = $2
        "#,
    )
    .bind(retracted_event_id)
    .bind(user_id)
    .fetch_optional(&mut **tx)
    .await?;

    if let Some(row) = resolved {
        return Ok((
            row.event_type,
            row.data,
            Some(format!(
                "event.retracted resolved via retracted_event_id={}",
                retracted_event_id
            )),
        ));
    }

    if let Some(event_type) = fallback_type {
        return Ok((
            event_type.clone(),
            serde_json::json!({}),
            Some(format!(
                "event.retracted fallback: retracted_event_id={} not found, used retracted_event_type='{}'.",
                retracted_event_id, event_type
            )),
        ));
    }

    Ok((
        "unknown".to_string(),
        serde_json::json!({}),
        Some(format!(
            "event.retracted target {} not found and no retracted_event_type provided; prediction may be incomplete.",
            retracted_event_id
        )),
    ))
}

/// Create a single event
///
/// Accepts an event and stores it immutably. The event_type is free-form —
/// new types emerge from usage, not from a hardcoded schema.
///
/// Response includes plausibility warnings when values look unusual.
/// Warnings are informational — the event is always accepted.
#[utoipa::path(
    post,
    path = "/v1/events",
    request_body = CreateEventRequest,
    responses(
        (status = 201, description = "Event created", body = CreateEventResponse),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 409, description = "Idempotency conflict", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "events"
)]
pub async fn create_event(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<CreateEventRequest>,
) -> Result<impl IntoResponse, AppError> {
    let user_id = auth.user_id;
    validate_event(&req)?;
    if event_requires_health_data_consent(&req.event_type) {
        ensure_health_data_processing_consent(&state, user_id).await?;
    }
    enforce_legacy_domain_invariants(&state, user_id, std::slice::from_ref(&req)).await?;

    let mut warnings = check_event_plausibility(&req.event_type, &req.data);

    // Exercise-ID similarity check (needs DB to fetch known IDs)
    let known_ids = fetch_user_exercise_ids(&state.db, user_id).await?;
    warnings.extend(check_exercise_id_similarity(
        &req.event_type,
        &req.data,
        &known_ids,
    ));

    let event = insert_event(&state.db, user_id, req).await?;

    Ok((
        StatusCode::CREATED,
        Json(CreateEventResponse { event, warnings }),
    ))
}

pub(crate) async fn create_events_batch_internal(
    state: &AppState,
    user_id: Uuid,
    events: &[CreateEventRequest],
) -> Result<BatchCreateEventsResponse, AppError> {
    if events.is_empty() {
        return Err(AppError::Validation {
            message: "events array must not be empty".to_string(),
            field: Some("events".to_string()),
            received: None,
            docs_hint: Some("Provide at least one event in the batch".to_string()),
        });
    }

    if events.len() > 100 {
        return Err(AppError::Validation {
            message: format!("Batch size {} exceeds maximum of 100", events.len()),
            field: Some("events".to_string()),
            received: Some(serde_json::json!(events.len())),
            docs_hint: Some("Split large batches into chunks of 100 or fewer".to_string()),
        });
    }

    // Fetch known exercise_ids once for the entire batch
    let mut known_ids = fetch_user_exercise_ids(&state.db, user_id).await?;

    // Validate all events before writing any
    let mut all_warnings: Vec<BatchEventWarning> = Vec::new();
    for (i, event) in events.iter().enumerate() {
        validate_event(event).map_err(|e| match e {
            AppError::Validation {
                message,
                field,
                received,
                docs_hint,
            } => AppError::Validation {
                message: format!("events[{}]: {}", i, message),
                field: field.map(|f| format!("events[{}].{}", i, f)),
                received,
                docs_hint,
            },
            AppError::PolicyViolation {
                code,
                message,
                field,
                received,
                docs_hint,
            } => AppError::PolicyViolation {
                code,
                message: format!("events[{}]: {}", i, message),
                field: field.map(|f| format!("events[{}].{}", i, f)),
                received,
                docs_hint,
            },
            other => other,
        })?;

        // Collect plausibility warnings per event
        for w in check_event_plausibility(&event.event_type, &event.data) {
            all_warnings.push(BatchEventWarning {
                event_index: i,
                field: w.field,
                message: w.message,
                severity: w.severity,
            });
        }

        // Exercise-ID similarity check
        for w in check_exercise_id_similarity(&event.event_type, &event.data, &known_ids) {
            all_warnings.push(BatchEventWarning {
                event_index: i,
                field: w.field,
                message: w.message,
                severity: w.severity,
            });
        }

        // Track new exercise_id from this event for subsequent events in batch
        if let Some(eid) = event.data.get("exercise_id").and_then(|v| v.as_str()) {
            let normalized = eid.trim().to_lowercase();
            if !normalized.is_empty() {
                known_ids.insert(normalized);
            }
        }
    }

    let mut tx = state.db.begin().await?;

    // Set RLS context for the entire batch transaction
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    // Prepare arrays for multi-row INSERT (avoids N+1 queries)
    let mut ids = Vec::with_capacity(events.len());
    let mut user_ids = Vec::with_capacity(events.len());
    let mut timestamps = Vec::with_capacity(events.len());
    let mut event_types = Vec::with_capacity(events.len());
    let mut data_values = Vec::with_capacity(events.len());
    let mut metadata_values = Vec::with_capacity(events.len());
    let mut idempotency_keys = Vec::with_capacity(events.len());

    for event_req in events {
        ids.push(Uuid::now_v7());
        user_ids.push(user_id);
        timestamps.push(event_req.timestamp);
        event_types.push(event_req.event_type.clone());
        data_values.push(event_req.data.clone());
        metadata_values.push(
            serde_json::to_value(&event_req.metadata)
                .map_err(|e| AppError::Internal(format!("Failed to serialize metadata: {}", e)))?,
        );
        idempotency_keys.push(event_req.metadata.idempotency_key.clone());
    }

    let rows = sqlx::query_as::<_, EventRow>(
        r#"
        INSERT INTO events (id, user_id, timestamp, event_type, data, metadata)
        SELECT * FROM UNNEST($1::uuid[], $2::uuid[], $3::timestamptz[], $4::text[], $5::jsonb[], $6::jsonb[])
        RETURNING id, user_id, timestamp, event_type, data, metadata, created_at
        "#,
    )
    .bind(&ids)
    .bind(&user_ids)
    .bind(&timestamps)
    .bind(&event_types)
    .bind(&data_values)
    .bind(&metadata_values)
    .fetch_all(&mut *tx)
    .await
    .map_err(|e| {
        if let sqlx::Error::Database(ref db_err) = e {
            if db_err.code().as_deref() == Some("23505") {
                // Find which idempotency key conflicted from error detail/message
                let pg_detail = db_err
                    .try_downcast_ref::<sqlx::postgres::PgDatabaseError>()
                    .and_then(|pg| pg.detail())
                    .unwrap_or_default();
                let search_text = format!("{} {}", db_err.message(), pg_detail);
                let key = idempotency_keys
                    .iter()
                    .find(|k| search_text.contains(k.as_str()))
                    .cloned()
                    .unwrap_or_else(|| "unknown".to_string());
                return AppError::IdempotencyConflict {
                    idempotency_key: key,
                };
            }
        }
        AppError::Database(e)
    })?;

    tx.commit().await?;

    let created_events: Vec<Event> = rows.into_iter().map(|r| r.into_event()).collect();
    Ok(BatchCreateEventsResponse {
        events: created_events,
        warnings: all_warnings,
    })
}

/// Create multiple events atomically
///
/// All events in the batch are written in a single transaction.
/// If any event fails validation or conflicts, the entire batch is rolled back.
/// Use this for complete training sessions (session.started + sets + session.ended).
///
/// Response includes plausibility warnings (with event_index) when values look unusual.
/// Warnings are informational — events are always accepted.
#[utoipa::path(
    post,
    path = "/v1/events/batch",
    request_body = BatchCreateEventsRequest,
    responses(
        (status = 201, description = "All events created", body = BatchCreateEventsResponse),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 409, description = "Idempotency conflict", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "events"
)]
pub async fn create_events_batch(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<BatchCreateEventsRequest>,
) -> Result<impl IntoResponse, AppError> {
    if batch_requires_health_data_consent(&req.events) {
        ensure_health_data_processing_consent(&state, auth.user_id).await?;
    }
    enforce_legacy_domain_invariants(&state, auth.user_id, &req.events).await?;
    let batch_result = create_events_batch_internal(&state, auth.user_id, &req.events).await?;
    Ok((StatusCode::CREATED, Json(batch_result)))
}

/// Simulate a batch of events without writing to the database.
///
/// Validates event payloads and returns predicted projection impacts:
/// - Which projection keys are likely to change
/// - Expected change mode (create/update/delete/noop/unknown)
/// - Predicted version increments (where determinable)
#[utoipa::path(
    post,
    path = "/v1/events/simulate",
    request_body = SimulateEventsRequest,
    responses(
        (status = 200, description = "Simulation result", body = SimulateEventsResponse),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "events"
)]
pub async fn simulate_events(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<SimulateEventsRequest>,
) -> Result<Json<SimulateEventsResponse>, AppError> {
    let user_id = auth.user_id;

    if req.events.is_empty() {
        return Err(AppError::Validation {
            message: "events array must not be empty".to_string(),
            field: Some("events".to_string()),
            received: None,
            docs_hint: Some("Provide at least one event in the simulation batch".to_string()),
        });
    }

    if req.events.len() > 100 {
        return Err(AppError::Validation {
            message: format!("Batch size {} exceeds maximum of 100", req.events.len()),
            field: Some("events".to_string()),
            received: Some(serde_json::json!(req.events.len())),
            docs_hint: Some(
                "Split large simulation batches into chunks of 100 or fewer".to_string(),
            ),
        });
    }

    if batch_requires_health_data_consent(&req.events) {
        ensure_health_data_processing_consent(&state, user_id).await?;
    }

    let mut known_ids = fetch_user_exercise_ids(&state.db, user_id).await?;
    let mut warnings: Vec<BatchEventWarning> = Vec::new();
    let mut candidates: HashMap<ProjectionTargetKey, ProjectionTargetCandidate> = HashMap::new();
    let mut notes: Vec<String> = Vec::new();

    let mut tx = state.db.begin().await?;

    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let active_custom_rules = load_active_custom_rules(&mut tx, user_id).await?;

    for (i, event) in req.events.iter().enumerate() {
        validate_event(event).map_err(|e| match e {
            AppError::Validation {
                message,
                field,
                received,
                docs_hint,
            } => AppError::Validation {
                message: format!("events[{}]: {}", i, message),
                field: field.map(|f| format!("events[{}].{}", i, f)),
                received,
                docs_hint,
            },
            AppError::PolicyViolation {
                code,
                message,
                field,
                received,
                docs_hint,
            } => AppError::PolicyViolation {
                code,
                message: format!("events[{}]: {}", i, message),
                field: field.map(|f| format!("events[{}].{}", i, f)),
                received,
                docs_hint,
            },
            other => other,
        })?;

        for w in check_event_plausibility(&event.event_type, &event.data) {
            warnings.push(BatchEventWarning {
                event_index: i,
                field: w.field,
                message: w.message,
                severity: w.severity,
            });
        }

        for w in check_exercise_id_similarity(&event.event_type, &event.data, &known_ids) {
            warnings.push(BatchEventWarning {
                event_index: i,
                field: w.field,
                message: w.message,
                severity: w.severity,
            });
        }

        if let Some(eid) = event.data.get("exercise_id").and_then(|v| v.as_str()) {
            let normalized = eid.trim().to_lowercase();
            if !normalized.is_empty() {
                known_ids.insert(normalized);
            }
        }

        let (resolved_event_type, resolved_data, resolution_note) =
            if event.event_type == "event.retracted" {
                resolve_retracted_event_for_simulation(&mut tx, user_id, &event.data).await?
            } else {
                (event.event_type.clone(), event.data.clone(), None)
            };

        if let Some(note) = resolution_note {
            notes.push(note);
        }

        let mut mapped =
            add_standard_projection_targets(&mut candidates, &resolved_event_type, &resolved_data);
        mapped |=
            add_custom_rule_targets(&mut candidates, &active_custom_rules, &resolved_event_type);

        if !mapped {
            notes.push(format!(
                "No projection handlers matched simulated event_type '{}'.",
                resolved_event_type
            ));
        }
    }

    let projection_types: HashSet<String> = candidates
        .iter()
        .filter(|(k, c)| k.key != "*" && !c.unknown_target)
        .map(|(k, _)| k.projection_type.clone())
        .collect();

    let existing_rows = if projection_types.is_empty() {
        Vec::new()
    } else {
        let projection_types: Vec<String> = projection_types.into_iter().collect();
        sqlx::query_as::<_, ExistingProjectionVersionRow>(
            r#"
            SELECT projection_type, key, version
            FROM projections
            WHERE user_id = $1
              AND projection_type = ANY($2)
            "#,
        )
        .bind(user_id)
        .bind(&projection_types)
        .fetch_all(&mut *tx)
        .await?
    };

    tx.commit().await?;

    let existing_versions: HashMap<(String, String), i64> = existing_rows
        .into_iter()
        .map(|row| ((row.projection_type, row.key), row.version))
        .collect();

    let mut projection_impacts: Vec<ProjectionImpact> = candidates
        .into_iter()
        .map(|(key, candidate)| {
            if candidate.unknown_target || key.key == "*" {
                return ProjectionImpact {
                    projection_type: key.projection_type,
                    key: key.key,
                    change: ProjectionImpactChange::Unknown,
                    current_version: None,
                    predicted_version: None,
                    reasons: candidate.reasons,
                };
            }

            let current_version = existing_versions
                .get(&(key.projection_type.clone(), key.key.clone()))
                .copied();

            let (change, predicted_version, mut reasons) = if candidate.delete_hint {
                match current_version {
                    Some(_) => (ProjectionImpactChange::Delete, None, candidate.reasons),
                    None => {
                        let mut reasons = candidate.reasons;
                        reasons.push(
                            "Projection does not exist yet; archive would be a no-op.".to_string(),
                        );
                        (ProjectionImpactChange::Noop, None, reasons)
                    }
                }
            } else {
                match current_version {
                    Some(v) => (
                        ProjectionImpactChange::Update,
                        Some(v + 1),
                        candidate.reasons,
                    ),
                    None => (ProjectionImpactChange::Create, Some(1), candidate.reasons),
                }
            };

            if reasons.is_empty() {
                reasons.push("No detailed reason captured for this projection impact.".to_string());
            }

            ProjectionImpact {
                projection_type: key.projection_type,
                key: key.key,
                change,
                current_version,
                predicted_version,
                reasons,
            }
        })
        .collect();

    projection_impacts.sort_by(|a, b| {
        a.projection_type
            .cmp(&b.projection_type)
            .then(a.key.cmp(&b.key))
    });

    notes.sort();
    notes.dedup();

    Ok(Json(SimulateEventsResponse {
        event_count: req.events.len(),
        warnings,
        projection_impacts,
        notes,
    }))
}

/// Query parameters for listing events
#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct ListEventsParams {
    /// Filter by event type (e.g. "set.logged", "meal.logged")
    #[serde(default)]
    pub event_type: Option<String>,
    /// Only events after this timestamp (inclusive)
    #[serde(default)]
    pub since: Option<DateTime<Utc>>,
    /// Only events before this timestamp (exclusive)
    #[serde(default)]
    pub until: Option<DateTime<Utc>>,
    /// Maximum number of events to return (default 50, max 200)
    #[serde(default)]
    pub limit: Option<i64>,
    /// Cursor for pagination (opaque string from previous response's next_cursor)
    #[serde(default)]
    pub cursor: Option<String>,
}

/// List events with cursor-based pagination
///
/// Returns events ordered by timestamp descending (newest first).
/// Use cursor-based pagination for stable iteration over growing data.
/// Filter by event_type and/or time range (since/until).
#[utoipa::path(
    get,
    path = "/v1/events",
    params(ListEventsParams),
    responses(
        (status = 200, description = "Paginated list of events", body = PaginatedResponse<Event>),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "events"
)]
pub async fn list_events(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Query(params): Query<ListEventsParams>,
) -> Result<impl IntoResponse, AppError> {
    let user_id = auth.user_id;

    let limit = params.limit.unwrap_or(50).min(200).max(1);
    // Fetch one extra to determine has_more
    let fetch_limit = limit + 1;

    // Decode cursor: it's a base64-encoded "timestamp,id" pair
    let cursor_data = if let Some(ref cursor_str) = params.cursor {
        Some(decode_cursor(cursor_str)?)
    } else {
        None
    };

    let mut tx = state.db.begin().await?;

    // Set RLS context
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    // Build query dynamically based on filters
    // We order by (timestamp DESC, id DESC) for stable cursor pagination
    let rows = if let Some(ref event_type) = params.event_type {
        if let Some(ref cursor) = cursor_data {
            sqlx::query_as::<_, EventRow>(
                r#"
                SELECT id, user_id, timestamp, event_type, data, metadata, created_at
                FROM events
                WHERE user_id = $1
                  AND event_type = $2
                  AND (timestamp, id) < ($3, $4)
                  AND ($5::timestamptz IS NULL OR timestamp >= $5)
                  AND ($6::timestamptz IS NULL OR timestamp < $6)
                ORDER BY timestamp DESC, id DESC
                LIMIT $7
                "#,
            )
            .bind(user_id)
            .bind(event_type)
            .bind(cursor.timestamp)
            .bind(cursor.id)
            .bind(params.since)
            .bind(params.until)
            .bind(fetch_limit)
            .fetch_all(&mut *tx)
            .await?
        } else {
            sqlx::query_as::<_, EventRow>(
                r#"
                SELECT id, user_id, timestamp, event_type, data, metadata, created_at
                FROM events
                WHERE user_id = $1
                  AND event_type = $2
                  AND ($3::timestamptz IS NULL OR timestamp >= $3)
                  AND ($4::timestamptz IS NULL OR timestamp < $4)
                ORDER BY timestamp DESC, id DESC
                LIMIT $5
                "#,
            )
            .bind(user_id)
            .bind(event_type)
            .bind(params.since)
            .bind(params.until)
            .bind(fetch_limit)
            .fetch_all(&mut *tx)
            .await?
        }
    } else if let Some(ref cursor) = cursor_data {
        sqlx::query_as::<_, EventRow>(
            r#"
            SELECT id, user_id, timestamp, event_type, data, metadata, created_at
            FROM events
            WHERE user_id = $1
              AND (timestamp, id) < ($2, $3)
              AND ($4::timestamptz IS NULL OR timestamp >= $4)
              AND ($5::timestamptz IS NULL OR timestamp < $5)
            ORDER BY timestamp DESC, id DESC
            LIMIT $6
            "#,
        )
        .bind(user_id)
        .bind(cursor.timestamp)
        .bind(cursor.id)
        .bind(params.since)
        .bind(params.until)
        .bind(fetch_limit)
        .fetch_all(&mut *tx)
        .await?
    } else {
        sqlx::query_as::<_, EventRow>(
            r#"
            SELECT id, user_id, timestamp, event_type, data, metadata, created_at
            FROM events
            WHERE user_id = $1
              AND ($2::timestamptz IS NULL OR timestamp >= $2)
              AND ($3::timestamptz IS NULL OR timestamp < $3)
            ORDER BY timestamp DESC, id DESC
            LIMIT $4
            "#,
        )
        .bind(user_id)
        .bind(params.since)
        .bind(params.until)
        .bind(fetch_limit)
        .fetch_all(&mut *tx)
        .await?
    };

    tx.commit().await?;

    let has_more = rows.len() as i64 > limit;
    let events: Vec<Event> = rows
        .into_iter()
        .take(limit as usize)
        .map(|r| r.into_event())
        .collect();

    let next_cursor = if has_more {
        events.last().map(|e| encode_cursor(&e.timestamp, &e.id))
    } else {
        None
    };

    let analysis_subject_id = get_or_create_analysis_subject_id(&state.db, user_id)
        .await
        .map_err(AppError::Database)?;

    let mut headers = HeaderMap::new();
    headers.insert(
        HeaderName::from_static("x-kura-analysis-subject"),
        HeaderValue::from_str(&analysis_subject_id)
            .map_err(|e| AppError::Internal(e.to_string()))?,
    );

    Ok((
        headers,
        Json(PaginatedResponse {
            data: events,
            next_cursor,
            has_more,
        }),
    ))
}

/// Cursor is base64("timestamp\0id") — opaque to the client, stable for pagination
fn encode_cursor(timestamp: &DateTime<Utc>, id: &Uuid) -> String {
    use base64::Engine;
    let raw = format!("{}\0{}", timestamp.to_rfc3339(), id);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(raw.as_bytes())
}

struct CursorData {
    timestamp: DateTime<Utc>,
    id: Uuid,
}

fn decode_cursor(cursor: &str) -> Result<CursorData, AppError> {
    use base64::Engine;
    let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(cursor)
        .map_err(|_| AppError::Validation {
            message: "Invalid cursor format".to_string(),
            field: Some("cursor".to_string()),
            received: Some(serde_json::Value::String(cursor.to_string())),
            docs_hint: Some("Use the next_cursor value from a previous response".to_string()),
        })?;

    let s = String::from_utf8(bytes).map_err(|_| AppError::Validation {
        message: "Invalid cursor encoding".to_string(),
        field: Some("cursor".to_string()),
        received: None,
        docs_hint: None,
    })?;

    let parts: Vec<&str> = s.splitn(2, '\0').collect();
    if parts.len() != 2 {
        return Err(AppError::Validation {
            message: "Invalid cursor structure".to_string(),
            field: Some("cursor".to_string()),
            received: None,
            docs_hint: Some("Use the next_cursor value from a previous response".to_string()),
        });
    }

    let timestamp = DateTime::parse_from_rfc3339(parts[0])
        .map(|t| t.with_timezone(&Utc))
        .map_err(|_| AppError::Validation {
            message: "Invalid cursor timestamp".to_string(),
            field: Some("cursor".to_string()),
            received: None,
            docs_hint: None,
        })?;

    let id = Uuid::parse_str(parts[1]).map_err(|_| AppError::Validation {
        message: "Invalid cursor id".to_string(),
        field: Some("cursor".to_string()),
        received: None,
        docs_hint: None,
    })?;

    Ok(CursorData { timestamp, id })
}

/// Internal row type for sqlx mapping
#[derive(sqlx::FromRow)]
struct EventRow {
    id: Uuid,
    user_id: Uuid,
    timestamp: chrono::DateTime<chrono::Utc>,
    event_type: String,
    data: serde_json::Value,
    metadata: serde_json::Value,
    #[allow(dead_code)]
    created_at: chrono::DateTime<chrono::Utc>,
}

impl EventRow {
    fn into_event(self) -> Event {
        let metadata: EventMetadata =
            serde_json::from_value(self.metadata).unwrap_or_else(|e| {
                tracing::warn!(event_id = %self.id, error = %e, "Failed to deserialize event metadata, using fallback");
                EventMetadata {
                    source: None,
                    agent: None,
                    device: None,
                    session_id: None,
                    idempotency_key: "unknown".to_string(),
                }
            });

        Event {
            id: self.id,
            user_id: self.user_id,
            timestamp: self.timestamp,
            event_type: self.event_type,
            data: self.data,
            metadata,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn make_request(event_type: &str, data: serde_json::Value) -> CreateEventRequest {
        CreateEventRequest {
            timestamp: Utc::now(),
            event_type: event_type.to_string(),
            data,
            metadata: EventMetadata {
                source: Some("test".to_string()),
                agent: Some("tests".to_string()),
                device: None,
                session_id: None,
                idempotency_key: "idem-test-1".to_string(),
            },
        }
    }

    fn assert_policy_violation(err: AppError, expected_code: &str, expected_field: &str) {
        match err {
            AppError::PolicyViolation {
                code,
                field,
                docs_hint,
                ..
            } => {
                assert_eq!(code, expected_code);
                assert_eq!(field.as_deref(), Some(expected_field));
                assert!(docs_hint.is_some());
            }
            other => panic!("Expected policy violation, got {:?}", other),
        }
    }

    fn make_user_profile(
        onboarding_closed: bool,
        override_active: bool,
        timezone: Option<&str>,
    ) -> Value {
        let mut preferences = serde_json::Map::new();
        if let Some(timezone) = timezone {
            preferences.insert("timezone".to_string(), json!(timezone));
        }
        json!({
            "user": {
                "preferences": preferences,
                "workflow_state": {
                    "onboarding_closed": onboarding_closed,
                    "override_active": override_active,
                }
            }
        })
    }

    #[test]
    fn test_retraction_requires_target_event_id() {
        let req = make_request("event.retracted", json!({"reason": "oops"}));
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(
            err,
            "inv_retraction_target_required",
            "data.retracted_event_id",
        );
    }

    #[test]
    fn test_retraction_requires_uuid_target() {
        let req = make_request(
            "event.retracted",
            json!({"retracted_event_id": "not-a-uuid", "retracted_event_type": "set.logged"}),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(
            err,
            "inv_retraction_target_invalid_uuid",
            "data.retracted_event_id",
        );
    }

    #[test]
    fn test_set_corrected_requires_non_empty_changed_fields() {
        let req = make_request(
            "set.corrected",
            json!({
                "target_event_id": Uuid::now_v7().to_string(),
                "changed_fields": {}
            }),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(
            err,
            "inv_set_correction_changed_fields_empty",
            "data.changed_fields",
        );
    }

    #[test]
    fn test_projection_rule_created_rejects_invalid_rule_type() {
        let req = make_request(
            "projection_rule.created",
            json!({
                "name": "my_rule",
                "rule_type": "weird_rule",
                "source_events": ["set.logged"],
                "fields": ["weight_kg"],
            }),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(err, "inv_projection_rule_type_invalid", "data.rule_type");
    }

    #[test]
    fn test_projection_rule_created_rejects_group_by_not_in_fields() {
        let req = make_request(
            "projection_rule.created",
            json!({
                "name": "readiness_by_modality",
                "rule_type": "categorized_tracking",
                "source_events": ["set.logged"],
                "fields": ["load_volume"],
                "group_by": "exercise_id",
            }),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(
            err,
            "inv_projection_rule_group_by_not_in_fields",
            "data.group_by",
        );
    }

    #[test]
    fn test_projection_rule_archived_requires_name() {
        let req = make_request("projection_rule.archived", json!({"name": ""}));
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(
            err,
            "inv_projection_rule_archive_name_required",
            "data.name",
        );
    }

    #[test]
    fn test_projection_rule_created_valid_payload_passes() {
        let req = make_request(
            "projection_rule.created",
            json!({
                "name": "rest_tracking",
                "rule_type": "field_tracking",
                "source_events": ["set.logged", "set.corrected"],
                "fields": ["rest_seconds", "rir"],
            }),
        );
        assert!(validate_event(&req).is_ok());
    }

    #[test]
    fn test_set_logged_accepts_decimal_comma_intensity_values() {
        let req = make_request(
            "set.logged",
            json!({"exercise": "Bench Press", "reps": 5, "rpe": "8,5", "rir": "1,5"}),
        );
        assert!(validate_event(&req).is_ok());
    }

    #[test]
    fn test_set_logged_rejects_non_numeric_rpe() {
        let req = make_request(
            "set.logged",
            json!({"exercise": "Bench Press", "reps": 5, "rpe": "hard"}),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(err, "inv_set_rpe_invalid_type", "data.rpe");
    }

    #[test]
    fn test_set_logged_rejects_out_of_range_rir() {
        let req = make_request(
            "set.logged",
            json!({"exercise": "Bench Press", "reps": 5, "rir": 11}),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(err, "inv_set_rir_out_of_range", "data.rir");
    }

    #[test]
    fn session_logged_contract_accepts_strength_without_hr() {
        let req = make_request(
            "session.logged",
            json!({
                "contract_version": "session.logged.v1",
                "session_meta": {
                    "sport": "strength",
                    "timezone": "Europe/Berlin"
                },
                "blocks": [
                    {
                        "block_type": "strength_set",
                        "dose": {
                            "work": {"reps": 5},
                            "recovery": {"duration_seconds": 120},
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
                ]
            }),
        );
        assert!(validate_event(&req).is_ok());
    }

    #[test]
    fn session_logged_contract_rejects_missing_intensity_anchors_for_performance_block() {
        let req = make_request(
            "session.logged",
            json!({
                "contract_version": "session.logged.v1",
                "session_meta": {
                    "sport": "running",
                    "timezone": "Europe/Berlin"
                },
                "blocks": [
                    {
                        "block_type": "interval_endurance",
                        "dose": {
                            "work": {"duration_seconds": 120},
                            "recovery": {"duration_seconds": 60},
                            "repeats": 8
                        }
                    }
                ]
            }),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(
            err,
            "inv_session_block_anchor_required",
            "data.blocks[0].intensity_anchors",
        );
    }

    #[test]
    fn session_logged_contract_accepts_not_applicable_anchor_status() {
        let req = make_request(
            "session.logged",
            json!({
                "contract_version": "session.logged.v1",
                "session_meta": {
                    "sport": "running",
                    "timezone": "Europe/Berlin"
                },
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
                ]
            }),
        );
        assert!(validate_event(&req).is_ok());
    }

    #[test]
    fn session_logged_contract_rejects_metric_without_measurement_state() {
        let req = make_request(
            "session.logged",
            json!({
                "contract_version": "session.logged.v1",
                "session_meta": {
                    "sport": "running",
                    "timezone": "Europe/Berlin"
                },
                "blocks": [
                    {
                        "block_type": "interval_endurance",
                        "dose": {
                            "work": {"duration_seconds": 120},
                            "recovery": {"duration_seconds": 60},
                            "repeats": 8
                        },
                        "intensity_anchors": [
                            {
                                "measurement_state": "measured",
                                "unit": "min_per_km",
                                "value": 4.0
                            }
                        ],
                        "metrics": {
                            "heart_rate_avg": {"unit": "bpm", "value": 160}
                        }
                    }
                ]
            }),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(
            err,
            "inv_session_block_metric_measurement_state_required",
            "data.blocks[0].metrics.heart_rate_avg",
        );
    }

    #[test]
    fn session_logged_contract_accepts_hybrid_mixed_blocks() {
        let req = make_request(
            "session.logged",
            json!({
                "contract_version": "session.logged.v1",
                "session_meta": {
                    "sport": "hybrid",
                    "timezone": "Europe/Berlin",
                    "session_id": "2026-02-14-hybrid-1"
                },
                "blocks": [
                    {
                        "block_type": "strength_set",
                        "dose": {
                            "work": {"reps": 5},
                            "recovery": {"duration_seconds": 120},
                            "repeats": 5
                        },
                        "intensity_anchors": [
                            {
                                "measurement_state": "measured",
                                "unit": "rpe",
                                "value": 8
                            }
                        ]
                    },
                    {
                        "block_type": "interval_endurance",
                        "dose": {
                            "work": {"duration_seconds": 120},
                            "recovery": {"duration_seconds": 60},
                            "repeats": 8
                        },
                        "intensity_anchors": [
                            {
                                "measurement_state": "measured",
                                "unit": "min_per_km",
                                "value": 4.0
                            },
                            {
                                "measurement_state": "measured",
                                "unit": "borg_cr10",
                                "value": 7
                            }
                        ],
                        "metrics": {
                            "heart_rate_avg": {"measurement_state": "not_measured"}
                        }
                    }
                ],
                "provenance": {
                    "source_type": "manual"
                }
            }),
        );
        assert!(validate_event(&req).is_ok());
    }

    #[test]
    fn test_training_plan_rejects_invalid_target_rpe_type() {
        let req = make_request(
            "training_plan.created",
            json!({
                "sessions": [{
                    "day": "monday",
                    "exercises": [{
                        "exercise_id": "bench_press",
                        "target_rpe": "hard"
                    }]
                }]
            }),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(
            err,
            "inv_training_plan_target_rpe_invalid_type",
            "data.sessions[0].exercises[0].target_rpe",
        );
    }

    #[test]
    fn test_training_plan_created_requires_sessions() {
        let req = make_request("training_plan.created", json!({"name": "Strength Block"}));
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(
            err,
            "inv_training_plan_sessions_required",
            "data.sessions",
        );
    }

    #[test]
    fn test_training_plan_created_rejects_empty_sessions() {
        let req = make_request(
            "training_plan.created",
            json!({
                "name": "Strength Block",
                "sessions": []
            }),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(err, "inv_training_plan_sessions_empty", "data.sessions");
    }

    #[test]
    fn test_training_plan_updated_rejects_empty_sessions() {
        let req = make_request(
            "training_plan.updated",
            json!({
                "plan_id": "strength-a",
                "sessions": []
            }),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(err, "inv_training_plan_sessions_empty", "data.sessions");
    }

    #[test]
    fn test_training_plan_rejects_blank_name() {
        let req = make_request(
            "training_plan.updated",
            json!({
                "plan_id": "strength-a",
                "name": "   "
            }),
        );
        let err = validate_event(&req).expect_err("expected policy violation");
        assert_policy_violation(err, "inv_training_plan_name_invalid", "data.name");
    }

    #[test]
    fn test_training_plan_accepts_missing_name_with_sessions() {
        let req = make_request(
            "training_plan.created",
            json!({
                "sessions": [{
                    "day": "monday",
                    "exercises": [{
                        "exercise_id": "bench_press",
                        "target_rir": 2.0
                    }]
                }]
            }),
        );
        assert!(validate_event(&req).is_ok());
    }

    #[test]
    fn test_training_plan_accepts_decimal_comma_target_rir() {
        let req = make_request(
            "training_plan.updated",
            json!({
                "sessions": [{
                    "day": "monday",
                    "exercises": [{
                        "exercise_id": "bench_press",
                        "target_rir": "2,5"
                    }]
                }]
            }),
        );
        assert!(validate_event(&req).is_ok());
    }

    #[test]
    fn test_training_plan_missing_name_warns() {
        let warnings = check_event_plausibility(
            "training_plan.created",
            &json!({
                "sessions": [{
                    "day": "monday",
                    "exercises": [{"exercise_id": "bench_press"}]
                }]
            }),
        );
        assert_eq!(warnings.len(), 1);
        assert_eq!(warnings[0].field, "name");
        assert_eq!(warnings[0].severity, "warning");
    }

    #[test]
    fn test_normal_set_no_warnings() {
        let w = check_event_plausibility("set.logged", &json!({"weight_kg": 80, "reps": 5}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_set_extreme_weight_warns() {
        let w = check_event_plausibility("set.logged", &json!({"weight_kg": 600, "reps": 5}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "weight_kg");
        assert_eq!(w[0].severity, "warning");
    }

    #[test]
    fn test_set_negative_reps_warns() {
        let w = check_event_plausibility("set.logged", &json!({"weight_kg": 80, "reps": -1}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "reps");
    }

    #[test]
    fn test_set_multiple_warnings() {
        let w = check_event_plausibility("set.logged", &json!({"weight_kg": -5, "reps": 200}));
        assert_eq!(w.len(), 2);
    }

    #[test]
    fn test_set_rpe_rir_contradiction_warns() {
        let w = check_event_plausibility("set.logged", &json!({"rpe": 9, "rir": 5}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "rpe_rir");
    }

    #[test]
    fn test_bodyweight_normal() {
        let w = check_event_plausibility("bodyweight.logged", &json!({"weight_kg": 82.5}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_bodyweight_extreme() {
        let w = check_event_plausibility("bodyweight.logged", &json!({"weight_kg": 500}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "weight_kg");
    }

    #[test]
    fn test_bodyweight_too_low() {
        let w = check_event_plausibility("bodyweight.logged", &json!({"weight_kg": 10}));
        assert_eq!(w.len(), 1);
    }

    #[test]
    fn test_meal_normal() {
        let w = check_event_plausibility(
            "meal.logged",
            &json!({
                "calories": 600, "protein_g": 40, "carbs_g": 70, "fat_g": 20
            }),
        );
        assert!(w.is_empty());
    }

    #[test]
    fn test_meal_extreme_calories() {
        let w = check_event_plausibility("meal.logged", &json!({"calories": 8000}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "calories");
    }

    #[test]
    fn test_meal_negative_macro() {
        let w = check_event_plausibility("meal.logged", &json!({"protein_g": -10}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "protein_g");
    }

    #[test]
    fn test_sleep_normal() {
        let w = check_event_plausibility("sleep.logged", &json!({"duration_hours": 7.5}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_sleep_extreme() {
        let w = check_event_plausibility("sleep.logged", &json!({"duration_hours": 25}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "duration_hours");
    }

    #[test]
    fn test_soreness_normal() {
        let w = check_event_plausibility("soreness.logged", &json!({"severity": 3}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_soreness_zero_valid() {
        let w = check_event_plausibility("soreness.logged", &json!({"severity": 0}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_soreness_ten_valid() {
        let w = check_event_plausibility("soreness.logged", &json!({"severity": 10.0}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_soreness_out_of_range_above() {
        let w = check_event_plausibility("soreness.logged", &json!({"severity": 11}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "severity");
    }

    #[test]
    fn test_soreness_out_of_range_below() {
        let w = check_event_plausibility("soreness.logged", &json!({"severity": -1}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "severity");
    }

    #[test]
    fn test_session_completed_normal() {
        let w = check_event_plausibility(
            "session.completed",
            &json!({"enjoyment": 8, "perceived_quality": 7, "perceived_exertion": 6}),
        );
        assert!(w.is_empty());
    }

    #[test]
    fn test_session_completed_enjoyment_out_of_range() {
        let w = check_event_plausibility("session.completed", &json!({"enjoyment": 15}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "enjoyment");
    }

    #[test]
    fn test_session_completed_pain_zero_valid() {
        let w = check_event_plausibility("session.completed", &json!({"pain_discomfort": 0}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_session_completed_pain_out_of_range() {
        let w = check_event_plausibility("session.completed", &json!({"pain_discomfort": 11}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "pain_discomfort");
    }

    #[test]
    fn test_energy_normal() {
        let w = check_event_plausibility("energy.logged", &json!({"level": 7}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_energy_out_of_range() {
        let w = check_event_plausibility("energy.logged", &json!({"level": 15}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "level");
    }

    #[test]
    fn test_recovery_daily_checkin_normal() {
        let w = check_event_plausibility(
            "recovery.daily_checkin",
            &json!({
                "bodyweight_kg": 78.4,
                "sleep_hours": 7.2,
                "soreness": 3,
                "motivation": 8,
                "hrv_rmssd": 62,
                "alcohol_last_night": "none",
                "training_yesterday": "hard"
            }),
        );
        assert!(w.is_empty());
    }

    #[test]
    fn test_recovery_daily_checkin_non_standard_training_tag_warns() {
        let w = check_event_plausibility(
            "recovery.daily_checkin",
            &json!({"training_yesterday": "very_hard"}),
        );
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "training_yesterday");
    }

    #[test]
    fn test_measurement_normal() {
        let w = check_event_plausibility("measurement.logged", &json!({"value_cm": 85.0}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_measurement_extreme() {
        let w = check_event_plausibility("measurement.logged", &json!({"value_cm": 500}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "value_cm");
    }

    #[test]
    fn test_unknown_event_type_no_warnings() {
        let w = check_event_plausibility("custom.event", &json!({"anything": 999999}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_missing_fields_no_warnings() {
        let w = check_event_plausibility("set.logged", &json!({"notes": "just a note"}));
        assert!(w.is_empty());
    }

    #[test]
    fn test_warning_severity_is_always_warning() {
        let w = check_event_plausibility("set.logged", &json!({"weight_kg": 999}));
        assert!(w.iter().all(|w| w.severity == "warning"));
    }

    #[test]
    fn test_legacy_domain_invariants_block_planning_without_phase_close() {
        let events = vec![make_request(
            "training_plan.updated",
            json!({"name": "push_pull_legs"}),
        )];
        let profile = make_user_profile(false, false, Some("Europe/Berlin"));
        let err = evaluate_legacy_domain_invariants(&events, Some(&profile))
            .expect_err("expected policy violation");
        assert_policy_violation(err, "inv_workflow_phase_required", "events");
    }

    #[test]
    fn test_legacy_domain_invariants_allow_planning_with_close_transition() {
        let events = vec![
            make_request(
                "workflow.onboarding.closed",
                json!({"reason": "onboarding complete"}),
            ),
            make_request(
                "projection_rule.created",
                json!({
                    "name": "readiness_by_week",
                    "rule_type": "field_tracking",
                    "source_events": ["set.logged"],
                    "fields": ["weight_kg"]
                }),
            ),
        ];
        let profile = make_user_profile(false, false, Some("Europe/Berlin"));
        assert!(evaluate_legacy_domain_invariants(&events, Some(&profile)).is_ok());
    }

    #[test]
    fn test_legacy_domain_invariants_block_plan_writes_on_legacy_path() {
        let events = vec![make_request(
            "training_plan.created",
            json!({"name": "new_plan"}),
        )];
        let profile = make_user_profile(true, false, Some("Europe/Berlin"));
        let err = evaluate_legacy_domain_invariants(&events, Some(&profile))
            .expect_err("expected policy violation");
        assert_policy_violation(err, "inv_plan_write_requires_write_with_proof", "events");
    }

    #[test]
    fn test_legacy_domain_invariants_require_timezone_for_temporal_writes() {
        let events = vec![make_request(
            "set.logged",
            json!({"exercise": "Squat", "reps": 5, "weight_kg": 100}),
        )];
        let profile = make_user_profile(true, false, None);
        let err = evaluate_legacy_domain_invariants(&events, Some(&profile))
            .expect_err("expected policy violation");
        assert_policy_violation(err, "inv_timezone_required_for_temporal_write", "events");
    }

    #[test]
    fn test_legacy_domain_invariants_allow_temporal_writes_with_event_timezone() {
        let events = vec![make_request(
            "set.logged",
            json!({
                "exercise": "Squat",
                "reps": 5,
                "weight_kg": 100,
                "timezone": "Europe/Berlin"
            }),
        )];
        let profile = make_user_profile(true, false, None);
        assert!(evaluate_legacy_domain_invariants(&events, Some(&profile)).is_ok());
    }

    #[test]
    fn test_legacy_domain_invariants_require_timezone_for_daily_checkin_writes() {
        let events = vec![make_request(
            "recovery.daily_checkin",
            json!({"sleep_hours": 7.2, "soreness": 3, "motivation": 8}),
        )];
        let profile = make_user_profile(true, false, None);
        let err = evaluate_legacy_domain_invariants(&events, Some(&profile))
            .expect_err("expected policy violation");
        assert_policy_violation(err, "inv_timezone_required_for_temporal_write", "events");
    }

    // --- Exercise-ID similarity tests ---

    fn known_ids(ids: &[&str]) -> HashSet<String> {
        ids.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn test_similarity_no_similar() {
        let ids = known_ids(&["barbell_back_squat", "bench_press", "deadlift"]);
        let w = check_exercise_id_similarity(
            "set.logged",
            &json!({"exercise_id": "overhead_press"}),
            &ids,
        );
        assert!(w.is_empty());
    }

    #[test]
    fn test_similarity_match_found() {
        // "lu_raise" is not similar enough to "lateral_raise" (jaro_winkler ~0.72)
        // so let's use a closer match
        let ids2 = known_ids(&["lateral_raise", "bench_press"]);
        let w2 = check_exercise_id_similarity(
            "set.logged",
            &json!({"exercise_id": "laterl_raise"}),
            &ids2,
        );
        assert_eq!(w2.len(), 1);
        assert_eq!(w2[0].field, "exercise_id");
        assert!(w2[0].message.contains("lateral_raise"));
    }

    #[test]
    fn test_similarity_existing_no_warning() {
        let ids = known_ids(&["bench_press", "deadlift"]);
        let w = check_exercise_id_similarity(
            "set.logged",
            &json!({"exercise_id": "bench_press"}),
            &ids,
        );
        assert!(w.is_empty());
    }

    #[test]
    fn test_similarity_case_insensitive() {
        let ids = known_ids(&["bench_press"]);
        let w = check_exercise_id_similarity(
            "set.logged",
            &json!({"exercise_id": "Bench_Press"}),
            &ids,
        );
        assert!(w.is_empty()); // normalized to lowercase, matches
    }

    #[test]
    fn test_similarity_irrelevant_event_type() {
        let ids = known_ids(&["bench_press"]);
        let w = check_exercise_id_similarity(
            "meal.logged",
            &json!({"exercise_id": "bench_pres"}),
            &ids,
        );
        assert!(w.is_empty());
    }

    #[test]
    fn test_similarity_alias_created_event_type() {
        let ids = known_ids(&["bench_press"]);
        let w = check_exercise_id_similarity(
            "exercise.alias_created",
            &json!({"exercise_id": "bench_pres"}),
            &ids,
        );
        assert_eq!(w.len(), 1);
        assert!(w[0].message.contains("bench_press"));
    }

    #[test]
    fn test_similarity_empty_exercise_id() {
        let ids = known_ids(&["bench_press"]);
        let w = check_exercise_id_similarity("set.logged", &json!({"exercise_id": ""}), &ids);
        assert!(w.is_empty());
    }

    #[test]
    fn test_similarity_missing_exercise_id() {
        let ids = known_ids(&["bench_press"]);
        let w = check_exercise_id_similarity("set.logged", &json!({"weight_kg": 80}), &ids);
        assert!(w.is_empty());
    }

    #[test]
    fn test_similarity_empty_known_ids() {
        let ids = known_ids(&[]);
        let w = check_exercise_id_similarity(
            "set.logged",
            &json!({"exercise_id": "bench_press"}),
            &ids,
        );
        assert!(w.is_empty());
    }

    #[test]
    fn test_similarity_message_format() {
        let ids = known_ids(&["bench_press", "bench_presse"]);
        let w =
            check_exercise_id_similarity("set.logged", &json!({"exercise_id": "bench_pres"}), &ids);
        assert_eq!(w.len(), 1);
        assert!(
            w[0].message
                .starts_with("New exercise_id 'bench_pres'. Similar existing:")
        );
        assert_eq!(w[0].severity, "warning");
    }

    #[test]
    fn test_extract_exercise_key_prefers_exercise_id() {
        let key = extract_exercise_key(&json!({
            "exercise_id": "Barbell_Back_Squat",
            "exercise": "Kniebeuge"
        }));
        assert_eq!(key.as_deref(), Some("barbell_back_squat"));
    }

    #[test]
    fn test_add_standard_projection_targets_for_set_logged() {
        let mut candidates: HashMap<ProjectionTargetKey, ProjectionTargetCandidate> =
            HashMap::new();
        let mapped = add_standard_projection_targets(
            &mut candidates,
            "set.logged",
            &json!({"exercise_id": "bench_press"}),
        );

        assert!(mapped);
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "exercise_progression".to_string(),
            key: "bench_press".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "readiness_inference".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "strength_inference".to_string(),
            key: "bench_press".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "causal_inference".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "semantic_memory".to_string(),
            key: "overview".to_string(),
        }));
    }

    #[test]
    fn test_add_standard_projection_targets_for_set_corrected() {
        let mut candidates: HashMap<ProjectionTargetKey, ProjectionTargetCandidate> =
            HashMap::new();
        let mapped = add_standard_projection_targets(
            &mut candidates,
            "set.corrected",
            &json!({"target_event_id": "abc"}),
        );

        assert!(mapped);
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "session_feedback".to_string(),
            key: "overview".to_string(),
        }));
        let key = ProjectionTargetKey {
            projection_type: "exercise_progression".to_string(),
            key: "*".to_string(),
        };
        assert!(candidates.contains_key(&key));
        assert!(
            candidates
                .get(&key)
                .map(|candidate| candidate.unknown_target)
                .unwrap_or(false)
        );
    }

    #[test]
    fn test_add_standard_projection_targets_for_session_logged() {
        let mut candidates: HashMap<ProjectionTargetKey, ProjectionTargetCandidate> =
            HashMap::new();
        let mapped = add_standard_projection_targets(
            &mut candidates,
            "session.logged",
            &json!({"contract_version": "session.logged.v1"}),
        );

        assert!(mapped);
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "training_timeline".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "session_feedback".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
        }));
    }

    #[test]
    fn test_add_standard_projection_targets_for_session_completed() {
        let mut candidates: HashMap<ProjectionTargetKey, ProjectionTargetCandidate> =
            HashMap::new();
        let mapped =
            add_standard_projection_targets(&mut candidates, "session.completed", &json!({}));

        assert!(mapped);
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "session_feedback".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
        }));
    }

    #[test]
    fn test_add_standard_projection_targets_for_recovery_daily_checkin() {
        let mut candidates: HashMap<ProjectionTargetKey, ProjectionTargetCandidate> =
            HashMap::new();
        let mapped = add_standard_projection_targets(
            &mut candidates,
            "recovery.daily_checkin",
            &json!({"sleep_hours": 7.2}),
        );

        assert!(mapped);
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "recovery".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "body_composition".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "readiness_inference".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "causal_inference".to_string(),
            key: "overview".to_string(),
        }));
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
        }));
    }

    #[test]
    fn test_add_standard_projection_targets_for_observation_logged() {
        let mut candidates: HashMap<ProjectionTargetKey, ProjectionTargetCandidate> =
            HashMap::new();
        let mapped = add_standard_projection_targets(
            &mut candidates,
            "observation.logged",
            &json!({"dimension": "Motivation Pre"}),
        );

        assert!(mapped);
        assert!(candidates.contains_key(&ProjectionTargetKey {
            projection_type: "open_observations".to_string(),
            key: "motivation_pre".to_string(),
        }));
    }

    #[test]
    fn test_add_standard_projection_targets_for_observation_logged_without_dimension() {
        let mut candidates: HashMap<ProjectionTargetKey, ProjectionTargetCandidate> =
            HashMap::new();
        let mapped =
            add_standard_projection_targets(&mut candidates, "observation.logged", &json!({}));

        assert!(mapped);
        let key = ProjectionTargetKey {
            projection_type: "open_observations".to_string(),
            key: "*".to_string(),
        };
        assert!(candidates.contains_key(&key));
        assert!(
            candidates
                .get(&key)
                .map(|candidate| candidate.unknown_target)
                .unwrap_or(false)
        );
    }

    #[test]
    fn test_add_standard_projection_targets_for_rule_archive_sets_delete_hint() {
        let mut candidates: HashMap<ProjectionTargetKey, ProjectionTargetCandidate> =
            HashMap::new();
        let mapped = add_standard_projection_targets(
            &mut candidates,
            "projection_rule.archived",
            &json!({"name": "hrv_tracking"}),
        );

        assert!(mapped);
        let candidate = candidates
            .get(&ProjectionTargetKey {
                projection_type: "custom".to_string(),
                key: "hrv_tracking".to_string(),
            })
            .expect("custom target should be present");
        assert!(candidate.delete_hint);
    }

    #[test]
    fn test_health_consent_required_for_core_training_event() {
        assert!(event_requires_health_data_consent("set.logged"));
    }

    #[test]
    fn test_health_consent_required_for_health_prefix() {
        assert!(event_requires_health_data_consent("health.symptom.logged"));
        assert!(event_requires_health_data_consent("recovery.daily_checkin"));
    }

    #[test]
    fn test_health_consent_not_required_for_projection_rule_events() {
        assert!(!event_requires_health_data_consent(
            "projection_rule.created"
        ));
        assert!(!event_requires_health_data_consent(
            "workflow.onboarding.closed"
        ));
    }

    #[test]
    fn health_consent_forbidden_error_contract_is_machine_readable() {
        match health_consent_required_error() {
            AppError::ForbiddenAction {
                error_code,
                next_action,
                next_action_url,
                ..
            } => {
                assert_eq!(error_code.as_deref(), Some(HEALTH_CONSENT_ERROR_CODE));
                assert_eq!(next_action.as_deref(), Some(HEALTH_CONSENT_NEXT_ACTION));
                assert_eq!(
                    next_action_url.as_deref(),
                    Some(HEALTH_CONSENT_SETTINGS_URL)
                );
            }
            _ => panic!("expected ForbiddenAction"),
        }
    }
}
