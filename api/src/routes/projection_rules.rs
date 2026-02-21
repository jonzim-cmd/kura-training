use std::collections::{HashMap, HashSet};

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use kura_core::error::ApiError;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/projection-rules", get(list_projection_rules))
        .route(
            "/v1/projection-rules/validate",
            post(validate_projection_rule),
        )
        .route(
            "/v1/projection-rules/preview",
            post(preview_projection_rule),
        )
        .route("/v1/projection-rules/apply", post(apply_projection_rule))
        .route(
            "/v1/projection-rules/{name}",
            delete(archive_projection_rule),
        )
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct ProjectionRulesResponse {
    pub rules: Vec<ProjectionRuleItem>,
}

#[derive(Serialize, utoipa::ToSchema, Clone)]
pub struct ProjectionRuleItem {
    pub name: String,
    #[serde(rename = "type")]
    pub rule_type: String,
    pub source_events: Vec<String>,
    pub fields: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub group_by: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Deserialize, Serialize, utoipa::ToSchema, Clone, PartialEq, Eq)]
pub struct ProjectionRuleDraft {
    pub name: String,
    #[serde(rename = "type")]
    pub rule_type: String,
    pub source_events: Vec<String>,
    pub fields: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub group_by: Option<String>,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct ProjectionRuleDraftRequest {
    pub rule: ProjectionRuleDraft,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
pub struct ApplyProjectionRuleRequest {
    pub rule: ProjectionRuleDraft,
    #[serde(default)]
    pub idempotency_key: Option<String>,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct ProjectionRuleValidationResponse {
    pub valid: bool,
    pub rule: ProjectionRuleDraft,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<String>,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct ProjectionRulePreviewEventType {
    pub event_type: String,
    pub count: i64,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct ProjectionRulePreviewField {
    pub field: String,
    pub present_in_events: i64,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct ProjectionRulePreviewCategory {
    pub category: String,
    pub count: i64,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct ProjectionRulePreviewResponse {
    pub rule: ProjectionRuleDraft,
    pub matching_events: i64,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub by_event_type: Vec<ProjectionRulePreviewEventType>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub fields_present: Vec<ProjectionRulePreviewField>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub categories: Option<Vec<ProjectionRulePreviewCategory>>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<String>,
}

#[derive(Serialize, utoipa::ToSchema, Clone, Copy)]
#[serde(rename_all = "snake_case")]
pub enum ProjectionRuleApplyStatus {
    Created,
    Updated,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct ProjectionRuleApplyResponse {
    pub status: ProjectionRuleApplyStatus,
    pub event_id: Uuid,
    pub idempotency_key: String,
    pub rule: ProjectionRuleDraft,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct ProjectionRuleArchiveResponse {
    pub status: String,
    pub event_id: Uuid,
    pub idempotency_key: String,
    pub name: String,
}

#[derive(sqlx::FromRow)]
struct RuleEventRow {
    id: Uuid,
    event_type: String,
    data: serde_json::Value,
    timestamp: DateTime<Utc>,
}

#[derive(sqlx::FromRow)]
struct EventTypeCountRow {
    event_type: String,
    count: i64,
}

#[derive(sqlx::FromRow)]
struct CategoryCountRow {
    category: String,
    count: i64,
}

#[derive(Clone)]
struct RuleState {
    item: ProjectionRuleItem,
}

fn validation_error(
    field: &str,
    message: impl Into<String>,
    received: Option<serde_json::Value>,
    docs_hint: Option<&str>,
) -> AppError {
    AppError::Validation {
        message: message.into(),
        field: Some(field.to_string()),
        received,
        docs_hint: docs_hint.map(str::to_string),
    }
}

fn normalize_rule_name_for_lookup(field: &str, raw: &str) -> Result<String, AppError> {
    let name = raw.trim();
    if name.is_empty() {
        return Err(validation_error(
            field,
            "name must not be empty",
            Some(serde_json::json!(raw)),
            Some("Use a stable key like 'hrv_tracking' or 'supplement_tracking'."),
        ));
    }

    Ok(name.to_string())
}

fn normalize_rule_name(raw: &str) -> Result<String, AppError> {
    let name = normalize_rule_name_for_lookup("rule.name", raw)?;

    if name.len() > 64 {
        return Err(validation_error(
            "rule.name",
            "name must be 64 characters or fewer",
            Some(serde_json::json!(name.len())),
            Some("Keep rule names short and stable."),
        ));
    }

    if !name
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.')
    {
        return Err(validation_error(
            "rule.name",
            "name contains invalid characters",
            Some(serde_json::json!(raw)),
            Some("Allowed characters: letters, numbers, '_', '-', '.'."),
        ));
    }

    Ok(name)
}

fn normalize_string_list(
    raw: Vec<String>,
    field: &str,
    lowercase: bool,
) -> Result<Vec<String>, AppError> {
    if raw.is_empty() {
        return Err(validation_error(
            field,
            format!("{} must not be empty", field.replace("rule.", "")),
            None,
            None,
        ));
    }

    let mut out = Vec::new();
    let mut seen = HashSet::new();

    for value in raw {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            return Err(validation_error(
                field,
                format!("{} contains an empty value", field.replace("rule.", "")),
                Some(serde_json::json!(value)),
                None,
            ));
        }

        let normalized = if lowercase {
            trimmed.to_lowercase()
        } else {
            trimmed.to_string()
        };

        if seen.insert(normalized.clone()) {
            out.push(normalized);
        }
    }

    if out.is_empty() {
        return Err(validation_error(
            field,
            format!("{} must not be empty", field.replace("rule.", "")),
            None,
            None,
        ));
    }

    Ok(out)
}

fn validate_and_normalize_rule(rule: ProjectionRuleDraft) -> Result<ProjectionRuleDraft, AppError> {
    let name = normalize_rule_name(&rule.name)?;
    let rule_type = rule.rule_type.trim().to_lowercase();

    if rule_type != "field_tracking" && rule_type != "categorized_tracking" {
        return Err(validation_error(
            "rule.type",
            "type must be 'field_tracking' or 'categorized_tracking'",
            Some(serde_json::json!(rule.rule_type)),
            None,
        ));
    }

    let source_events = normalize_string_list(rule.source_events, "rule.source_events", true)?;
    let fields = normalize_string_list(rule.fields, "rule.fields", false)?;

    let group_by = match rule.group_by {
        Some(raw) => {
            let trimmed = raw.trim();
            if trimmed.is_empty() {
                return Err(validation_error(
                    "rule.group_by",
                    "group_by must not be empty",
                    Some(serde_json::json!(raw)),
                    None,
                ));
            }
            Some(trimmed.to_string())
        }
        None => None,
    };

    if rule_type == "field_tracking" && group_by.is_some() {
        return Err(validation_error(
            "rule.group_by",
            "group_by is only allowed for categorized_tracking rules",
            group_by.as_ref().map(|g| serde_json::json!(g)),
            None,
        ));
    }

    if rule_type == "categorized_tracking" {
        let Some(group_by_field) = group_by.as_ref() else {
            return Err(validation_error(
                "rule.group_by",
                "group_by is required for categorized_tracking rules",
                None,
                None,
            ));
        };

        if !fields.iter().any(|f| f == group_by_field) {
            return Err(validation_error(
                "rule.group_by",
                "group_by must be one of the declared fields",
                Some(serde_json::json!(group_by_field)),
                None,
            ));
        }
    }

    Ok(ProjectionRuleDraft {
        name,
        rule_type,
        source_events,
        fields,
        group_by,
    })
}

fn json_string_array(value: Option<&serde_json::Value>) -> Vec<String> {
    let Some(arr) = value.and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    arr.iter()
        .filter_map(|v| v.as_str().map(|s| s.to_string()))
        .collect()
}

fn build_rule_event_data(rule: &ProjectionRuleDraft) -> serde_json::Value {
    let mut map = serde_json::Map::new();
    map.insert("name".to_string(), serde_json::json!(rule.name));
    map.insert("type".to_string(), serde_json::json!(rule.rule_type));
    map.insert("rule_type".to_string(), serde_json::json!(rule.rule_type));
    map.insert(
        "source_events".to_string(),
        serde_json::json!(rule.source_events),
    );
    map.insert("fields".to_string(), serde_json::json!(rule.fields));

    if let Some(group_by) = &rule.group_by {
        map.insert("group_by".to_string(), serde_json::json!(group_by));
    }

    serde_json::Value::Object(map)
}

fn generated_idempotency_key(action: &str, rule_name: &str) -> String {
    format!(
        "projection_rule:{}:{}:{}",
        action,
        rule_name,
        Uuid::now_v7()
    )
}

async fn set_user_context(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
) -> Result<(), AppError> {
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn fetch_retracted_ids(
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

async fn load_active_rules(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
) -> Result<HashMap<String, RuleState>, AppError> {
    let retracted_ids = fetch_retracted_ids(tx, user_id).await?;

    let rows = sqlx::query_as::<_, RuleEventRow>(
        r#"
        SELECT id, event_type, data, timestamp
        FROM events
        WHERE user_id = $1
          AND event_type IN ('projection_rule.created', 'projection_rule.archived')
        ORDER BY timestamp ASC, id ASC
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **tx)
    .await?;

    let mut active: HashMap<String, RuleState> = HashMap::new();

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

        let created_at = active
            .get(&name)
            .map(|state| state.item.created_at)
            .unwrap_or(row.timestamp);

        let rule_type = row
            .data
            .get("type")
            .or_else(|| row.data.get("rule_type"))
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        let source_events = json_string_array(row.data.get("source_events"));
        let fields = json_string_array(row.data.get("fields"));
        let group_by = row
            .data
            .get("group_by")
            .and_then(|v| v.as_str())
            .map(str::to_string);

        let item = ProjectionRuleItem {
            name: name.clone(),
            rule_type,
            source_events,
            fields,
            group_by,
            created_at,
            updated_at: row.timestamp,
        };

        active.insert(name, RuleState { item });
    }

    Ok(active)
}

async fn insert_rule_event(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
    event_type: &str,
    data: serde_json::Value,
    idempotency_key: &str,
) -> Result<Uuid, AppError> {
    let event_id = Uuid::now_v7();
    let timestamp = Utc::now();
    let metadata = serde_json::json!({
        "source": "projection_rules_api",
        "agent": "kura-api",
        "idempotency_key": idempotency_key,
    });

    sqlx::query(
        r#"
        INSERT INTO events (id, user_id, timestamp, event_type, data, metadata)
        VALUES ($1, $2, $3, $4, $5, $6)
        "#,
    )
    .bind(event_id)
    .bind(user_id)
    .bind(timestamp)
    .bind(event_type)
    .bind(data)
    .bind(metadata)
    .execute(&mut **tx)
    .await
    .map_err(|e| {
        if let sqlx::Error::Database(ref db_err) = e {
            if db_err.code().as_deref() == Some("23505") {
                return AppError::IdempotencyConflict {
                    idempotency_key: idempotency_key.to_string(),
                };
            }
        }
        AppError::Database(e)
    })?;

    Ok(event_id)
}

/// List active projection rules for the authenticated user.
///
/// Rules are event-sourced:
/// - projection_rule.created activates/updates a rule
/// - projection_rule.archived deactivates a rule
///
/// Retracted rule events are ignored.
#[utoipa::path(
    get,
    path = "/v1/projection-rules",
    responses(
        (status = 200, description = "Active projection rules", body = ProjectionRulesResponse),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "projections"
)]
pub async fn list_projection_rules(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
) -> Result<Json<ProjectionRulesResponse>, AppError> {
    let user_id = auth.user_id;

    let mut tx = state.db.begin().await?;
    set_user_context(&mut tx, user_id).await?;

    let active = load_active_rules(&mut tx, user_id).await?;

    tx.commit().await?;

    let mut rules: Vec<ProjectionRuleItem> = active.into_values().map(|s| s.item).collect();
    rules.sort_by(|a, b| a.name.cmp(&b.name));

    Ok(Json(ProjectionRulesResponse { rules }))
}

/// Validate a projection rule draft without creating any event.
#[utoipa::path(
    post,
    path = "/v1/projection-rules/validate",
    request_body = ProjectionRuleDraftRequest,
    responses(
        (status = 200, description = "Rule is valid", body = ProjectionRuleValidationResponse),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "projections"
)]
pub async fn validate_projection_rule(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<ProjectionRuleDraftRequest>,
) -> Result<Json<ProjectionRuleValidationResponse>, AppError> {
    let user_id = auth.user_id;
    let rule = validate_and_normalize_rule(req.rule)?;

    let mut tx = state.db.begin().await?;
    set_user_context(&mut tx, user_id).await?;

    let active = load_active_rules(&mut tx, user_id).await?;

    tx.commit().await?;

    let mut warnings = Vec::new();
    if active.contains_key(&rule.name) {
        warnings.push(format!(
            "Rule '{}' already exists and would be updated by apply.",
            rule.name
        ));
    }

    Ok(Json(ProjectionRuleValidationResponse {
        valid: true,
        rule,
        warnings,
    }))
}

/// Preview how many existing events a projection rule would match.
#[utoipa::path(
    post,
    path = "/v1/projection-rules/preview",
    request_body = ProjectionRuleDraftRequest,
    responses(
        (status = 200, description = "Rule preview", body = ProjectionRulePreviewResponse),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "projections"
)]
pub async fn preview_projection_rule(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<ProjectionRuleDraftRequest>,
) -> Result<Json<ProjectionRulePreviewResponse>, AppError> {
    let user_id = auth.user_id;
    let rule = validate_and_normalize_rule(req.rule)?;

    let mut tx = state.db.begin().await?;
    set_user_context(&mut tx, user_id).await?;

    let active = load_active_rules(&mut tx, user_id).await?;
    let retracted_ids: Vec<Uuid> = fetch_retracted_ids(&mut tx, user_id)
        .await?
        .into_iter()
        .collect();

    let matching_events: i64 = sqlx::query_scalar(
        r#"
        SELECT COUNT(*)
        FROM events
        WHERE user_id = $1
          AND event_type = ANY($2)
          AND id <> ALL($3::uuid[])
        "#,
    )
    .bind(user_id)
    .bind(&rule.source_events)
    .bind(&retracted_ids)
    .fetch_one(&mut *tx)
    .await?;

    let by_event_type_rows = sqlx::query_as::<_, EventTypeCountRow>(
        r#"
        SELECT event_type, COUNT(*)::bigint AS count
        FROM events
        WHERE user_id = $1
          AND event_type = ANY($2)
          AND id <> ALL($3::uuid[])
        GROUP BY event_type
        ORDER BY count DESC, event_type ASC
        "#,
    )
    .bind(user_id)
    .bind(&rule.source_events)
    .bind(&retracted_ids)
    .fetch_all(&mut *tx)
    .await?;

    let by_event_type: Vec<ProjectionRulePreviewEventType> = by_event_type_rows
        .iter()
        .map(|row| ProjectionRulePreviewEventType {
            event_type: row.event_type.clone(),
            count: row.count,
        })
        .collect();

    let mut fields_present = Vec::new();
    for field in &rule.fields {
        let present_in_events: i64 = sqlx::query_scalar(
            r#"
            SELECT COUNT(*)
            FROM events
            WHERE user_id = $1
              AND event_type = ANY($2)
              AND id <> ALL($3::uuid[])
              AND data ? $4
            "#,
        )
        .bind(user_id)
        .bind(&rule.source_events)
        .bind(&retracted_ids)
        .bind(field)
        .fetch_one(&mut *tx)
        .await?;

        fields_present.push(ProjectionRulePreviewField {
            field: field.clone(),
            present_in_events,
        });
    }

    let categories = if rule.rule_type == "categorized_tracking" {
        let group_by = rule.group_by.as_deref().unwrap_or_default();
        let rows = sqlx::query_as::<_, CategoryCountRow>(
            r#"
            SELECT
                COALESCE(NULLIF(lower(trim(data->>$4)), ''), '_unknown') AS category,
                COUNT(*)::bigint AS count
            FROM events
            WHERE user_id = $1
              AND event_type = ANY($2)
              AND id <> ALL($3::uuid[])
            GROUP BY category
            ORDER BY count DESC, category ASC
            LIMIT 10
            "#,
        )
        .bind(user_id)
        .bind(&rule.source_events)
        .bind(&retracted_ids)
        .bind(group_by)
        .fetch_all(&mut *tx)
        .await?;

        Some(
            rows.into_iter()
                .map(|row| ProjectionRulePreviewCategory {
                    category: row.category,
                    count: row.count,
                })
                .collect(),
        )
    } else {
        None
    };

    tx.commit().await?;

    let mut warnings = Vec::new();
    if matching_events == 0 {
        warnings.push(
            "No matching events found. Applying this rule will create an empty projection until matching events arrive."
                .to_string(),
        );
    }

    let matched_types: HashSet<&str> = by_event_type
        .iter()
        .map(|r| r.event_type.as_str())
        .collect();
    for event_type in &rule.source_events {
        if !matched_types.contains(event_type.as_str()) {
            warnings.push(format!(
                "No events currently match source event '{}'.",
                event_type
            ));
        }
    }

    if active.contains_key(&rule.name) {
        warnings.push(format!(
            "Rule '{}' already exists and would be replaced on apply.",
            rule.name
        ));
    }

    Ok(Json(ProjectionRulePreviewResponse {
        rule,
        matching_events,
        by_event_type,
        fields_present,
        categories,
        warnings,
    }))
}

/// Apply (create/update) a projection rule by emitting `projection_rule.created`.
#[utoipa::path(
    post,
    path = "/v1/projection-rules/apply",
    request_body = ApplyProjectionRuleRequest,
    responses(
        (status = 201, description = "Rule created", body = ProjectionRuleApplyResponse),
        (status = 200, description = "Rule updated", body = ProjectionRuleApplyResponse),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 409, description = "Idempotency conflict", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "projections"
)]
pub async fn apply_projection_rule(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<ApplyProjectionRuleRequest>,
) -> Result<impl IntoResponse, AppError> {
    let user_id = auth.user_id;
    let rule = validate_and_normalize_rule(req.rule)?;

    let idempotency_key = match req.idempotency_key {
        Some(key) => {
            let trimmed = key.trim();
            if trimmed.is_empty() {
                return Err(validation_error(
                    "idempotency_key",
                    "idempotency_key must not be empty",
                    None,
                    Some("Omit idempotency_key to auto-generate one."),
                ));
            }
            trimmed.to_string()
        }
        None => generated_idempotency_key("apply", &rule.name),
    };

    let mut tx = state.db.begin().await?;
    set_user_context(&mut tx, user_id).await?;

    let active = load_active_rules(&mut tx, user_id).await?;
    let status = if active.contains_key(&rule.name) {
        ProjectionRuleApplyStatus::Updated
    } else {
        ProjectionRuleApplyStatus::Created
    };

    let event_id = insert_rule_event(
        &mut tx,
        user_id,
        "projection_rule.created",
        build_rule_event_data(&rule),
        &idempotency_key,
    )
    .await?;

    tx.commit().await?;

    let status_code = match status {
        ProjectionRuleApplyStatus::Created => StatusCode::CREATED,
        ProjectionRuleApplyStatus::Updated => StatusCode::OK,
    };

    Ok((
        status_code,
        Json(ProjectionRuleApplyResponse {
            status,
            event_id,
            idempotency_key,
            rule,
        }),
    ))
}

/// Archive a projection rule by emitting `projection_rule.archived`.
#[utoipa::path(
    delete,
    path = "/v1/projection-rules/{name}",
    params(("name" = String, Path, description = "Rule name to archive")),
    responses(
        (status = 200, description = "Rule archived", body = ProjectionRuleArchiveResponse),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 404, description = "Rule not found", body = ApiError),
        (status = 409, description = "Idempotency conflict", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "projections"
)]
pub async fn archive_projection_rule(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path(name): Path<String>,
) -> Result<Json<ProjectionRuleArchiveResponse>, AppError> {
    let user_id = auth.user_id;
    let normalized_name = normalize_rule_name_for_lookup("name", &name)?;
    let idempotency_key = generated_idempotency_key("archive", &normalized_name);

    let mut tx = state.db.begin().await?;
    set_user_context(&mut tx, user_id).await?;

    let active = load_active_rules(&mut tx, user_id).await?;
    if !active.contains_key(&normalized_name) {
        return Err(AppError::NotFound {
            resource: format!("projection rule {}", normalized_name),
        });
    }

    let event_id = insert_rule_event(
        &mut tx,
        user_id,
        "projection_rule.archived",
        serde_json::json!({"name": normalized_name}),
        &idempotency_key,
    )
    .await?;

    tx.commit().await?;

    Ok(Json(ProjectionRuleArchiveResponse {
        status: "archived".to_string(),
        event_id,
        idempotency_key,
        name: normalized_name,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rule(
        name: &str,
        rule_type: &str,
        source_events: &[&str],
        fields: &[&str],
        group_by: Option<&str>,
    ) -> ProjectionRuleDraft {
        ProjectionRuleDraft {
            name: name.to_string(),
            rule_type: rule_type.to_string(),
            source_events: source_events.iter().map(|s| s.to_string()).collect(),
            fields: fields.iter().map(|s| s.to_string()).collect(),
            group_by: group_by.map(|s| s.to_string()),
        }
    }

    #[test]
    fn validate_normalizes_field_tracking_rule() {
        let input = rule(
            "  hrv_tracking  ",
            " FIELD_TRACKING ",
            &[" sleep.logged ", "sleep.logged"],
            &[" hrv_rmssd ", "hrv_rmssd", "deep_sleep_pct"],
            None,
        );

        let normalized = validate_and_normalize_rule(input).expect("rule should validate");
        assert_eq!(normalized.name, "hrv_tracking");
        assert_eq!(normalized.rule_type, "field_tracking");
        assert_eq!(normalized.source_events, vec!["sleep.logged"]);
        assert_eq!(
            normalized.fields,
            vec!["hrv_rmssd".to_string(), "deep_sleep_pct".to_string()]
        );
        assert!(normalized.group_by.is_none());
    }

    #[test]
    fn validate_accepts_categorized_tracking_rule() {
        let input = rule(
            "supplement_tracking",
            "categorized_tracking",
            &["supplement.logged"],
            &["name", "dose_mg"],
            Some("name"),
        );

        let normalized = validate_and_normalize_rule(input).expect("rule should validate");
        assert_eq!(normalized.rule_type, "categorized_tracking");
        assert_eq!(normalized.group_by.as_deref(), Some("name"));
    }

    #[test]
    fn validate_rejects_unknown_rule_type() {
        let input = rule(
            "x",
            "weird_tracking",
            &["sleep.logged"],
            &["hrv_rmssd"],
            None,
        );

        assert!(matches!(
            validate_and_normalize_rule(input),
            Err(AppError::Validation { .. })
        ));
    }

    #[test]
    fn validate_rejects_missing_group_by_for_categorized() {
        let input = rule(
            "supplement_tracking",
            "categorized_tracking",
            &["supplement.logged"],
            &["name", "dose_mg"],
            None,
        );

        assert!(matches!(
            validate_and_normalize_rule(input),
            Err(AppError::Validation { .. })
        ));
    }

    #[test]
    fn validate_rejects_group_by_not_in_fields() {
        let input = rule(
            "supplement_tracking",
            "categorized_tracking",
            &["supplement.logged"],
            &["dose_mg"],
            Some("name"),
        );

        assert!(matches!(
            validate_and_normalize_rule(input),
            Err(AppError::Validation { .. })
        ));
    }

    #[test]
    fn validate_rejects_invalid_rule_name() {
        let input = rule(
            "hrv tracking!",
            "field_tracking",
            &["sleep.logged"],
            &["hrv_rmssd"],
            None,
        );

        assert!(matches!(
            validate_and_normalize_rule(input),
            Err(AppError::Validation { .. })
        ));
    }
}
