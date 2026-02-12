use std::collections::{HashMap, HashSet};

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::events::{
    BatchCreateEventsRequest, BatchCreateEventsResponse, BatchEventWarning, CreateEventRequest,
    CreateEventResponse, Event, EventMetadata, EventWarning, PaginatedResponse, ProjectionImpact,
    ProjectionImpactChange, SimulateEventsRequest, SimulateEventsResponse,
};

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
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
        "projection_rule.created" => validate_projection_rule_created_invariants(&req.data),
        "projection_rule.archived" => validate_projection_rule_archived_invariants(&req.data),
        _ => Ok(()),
    }
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
            if let Some(s) = data.get("severity").and_then(|v| v.as_i64()) {
                if s < 1 || s > 5 {
                    warnings.push(EventWarning {
                        field: "severity".to_string(),
                        message: format!("severity={s} outside plausible range [1, 5]"),
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
) -> Result<Json<PaginatedResponse<Event>>, AppError> {
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

    Ok(Json(PaginatedResponse {
        data: events,
        next_cursor,
        has_more,
    }))
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
    fn test_soreness_out_of_range() {
        let w = check_event_plausibility("soreness.logged", &json!({"severity": 0}));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].field, "severity");
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
}
