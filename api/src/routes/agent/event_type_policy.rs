use super::*;

pub(super) const FORMAL_EVENT_TYPE_POLICY_SCHEMA_VERSION: &str = "formal_event_type_policy.v1";

pub(super) const DEFAULT_AGENT_FORMAL_EVENT_TYPES: &[&str] = &[
    "set.logged",
    "set.corrected",
    "session.logged",
    "session.completed",
    "training_plan.created",
    "training_plan.updated",
    "exercise.alias_created",
    "goal.set",
    "profile.updated",
    "injury.reported",
    "bodyweight.logged",
    "measurement.logged",
    "meal.logged",
    "sleep.logged",
    "soreness.logged",
    "energy.logged",
    "recovery.daily_checkin",
    "preference.set",
    "observation.logged",
    "event.retracted",
    "projection_rule.created",
    "projection_rule.archived",
    "quality.save_claim.checked",
    "quality.consistency.review.decided",
    "learning.signal.logged",
];

pub(super) fn extract_known_event_types_from_event_conventions(value: &Value) -> HashSet<String> {
    let mut known = HashSet::new();
    if let Some(map) = value.as_object() {
        for key in map.keys() {
            let normalized = key.trim().to_lowercase();
            if !normalized.is_empty() {
                known.insert(normalized);
            }
        }
    } else if let Some(items) = value.as_array() {
        for item in items {
            let normalized = item
                .get("event_type")
                .and_then(Value::as_str)
                .map(str::trim)
                .map(str::to_lowercase)
                .unwrap_or_default();
            if !normalized.is_empty() {
                known.insert(normalized);
            }
        }
    }
    known
}

pub(super) async fn fetch_known_event_types_from_system_config(
    state: &AppState,
) -> Result<HashSet<String>, AppError> {
    let mut known =
        sqlx::query_scalar::<_, Value>("SELECT data FROM system_config WHERE key = 'global'")
            .fetch_optional(&state.db)
            .await?
            .and_then(|data| data.get("event_conventions").cloned())
            .map(|conventions| extract_known_event_types_from_event_conventions(&conventions))
            .unwrap_or_default();

    if known.is_empty() {
        for event_type in DEFAULT_AGENT_FORMAL_EVENT_TYPES {
            known.insert((*event_type).to_string());
        }
    }
    Ok(known)
}

pub(super) fn is_formal_event_type_shape(event_type: &str) -> bool {
    static FORMAL_EVENT_TYPE_RE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
            .expect("formal event_type regex must compile")
    });
    FORMAL_EVENT_TYPE_RE.is_match(event_type)
}

pub(super) fn validate_registered_formal_event_type(
    event_type: &str,
    known_event_types: &HashSet<String>,
    field: &str,
) -> Result<(), AppError> {
    if !is_formal_event_type_shape(event_type) {
        return Err(AppError::Validation {
            message: "event_type must use formal dotted syntax (e.g. set.logged)".to_string(),
            field: Some(field.to_string()),
            received: Some(Value::String(event_type.to_string())),
            docs_hint: Some(
                "Use lowercase dotted event types like set.logged, session.completed, or training_plan.updated."
                    .to_string(),
            ),
        });
    }
    if known_event_types.contains(event_type) {
        return Ok(());
    }
    Err(AppError::PolicyViolation {
        code: "formal_event_type_unknown".to_string(),
        message: format!(
            "event_type '{}' is not registered in event_conventions and may not project reliably",
            event_type
        ),
        field: Some(field.to_string()),
        received: Some(json!({
            "event_type": event_type,
            "policy_schema_version": FORMAL_EVENT_TYPE_POLICY_SCHEMA_VERSION,
        })),
        docs_hint: Some(
            "Use a registered event_type from system_config.event_conventions or route the note through observation drafts."
                .to_string(),
        ),
    })
}
