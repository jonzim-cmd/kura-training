use std::collections::{HashMap, HashSet};

use axum::extract::State;
use axum::routing::get;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::Serialize;
use uuid::Uuid;

use kura_core::error::ApiError;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new().route("/v1/projection-rules", get(list_projection_rules))
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

#[derive(sqlx::FromRow)]
struct RuleEventRow {
    id: Uuid,
    event_type: String,
    data: serde_json::Value,
    timestamp: DateTime<Utc>,
}

#[derive(Clone)]
struct RuleState {
    item: ProjectionRuleItem,
}

fn json_string_array(value: Option<&serde_json::Value>) -> Vec<String> {
    let Some(arr) = value.and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    arr.iter()
        .filter_map(|v| v.as_str().map(|s| s.to_string()))
        .collect()
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

    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let retracted_ids: HashSet<Uuid> = sqlx::query_scalar::<_, String>(
        r#"
        SELECT data->>'retracted_event_id'
        FROM events
        WHERE user_id = $1
          AND event_type = 'event.retracted'
          AND data->>'retracted_event_id' IS NOT NULL
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut *tx)
    .await?
    .into_iter()
    .filter_map(|s| Uuid::parse_str(&s).ok())
    .collect();

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
    .fetch_all(&mut *tx)
    .await?;

    tx.commit().await?;

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
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        let source_events = json_string_array(row.data.get("source_events"));
        let fields = json_string_array(row.data.get("fields"));
        let group_by = row
            .data
            .get("group_by")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

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

    let mut rules: Vec<ProjectionRuleItem> =
        active.into_values().map(|state| state.item).collect();
    rules.sort_by(|a, b| a.name.cmp(&b.name));

    Ok(Json(ProjectionRulesResponse { rules }))
}
