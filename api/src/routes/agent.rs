use axum::extract::{Query, State};
use axum::routing::get;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::projections::{Projection, ProjectionMeta, ProjectionResponse};

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::routes::system::SystemConfigResponse;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new().route("/v1/agent/context", get(get_agent_context))
}

#[derive(Deserialize, utoipa::IntoParams)]
pub struct AgentContextParams {
    /// Maximum number of exercise_progression projections to include (default 5, max 100)
    #[serde(default)]
    pub exercise_limit: Option<i64>,
    /// Maximum number of custom projections to include (default 10, max 100)
    #[serde(default)]
    pub custom_limit: Option<i64>,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct AgentContextMeta {
    pub generated_at: DateTime<Utc>,
    pub exercise_limit: i64,
    pub custom_limit: i64,
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct AgentContextResponse {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub system: Option<SystemConfigResponse>,
    pub user_profile: ProjectionResponse,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub training_timeline: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub body_composition: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recovery: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub nutrition: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub training_plan: Option<ProjectionResponse>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub exercise_progression: Vec<ProjectionResponse>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub custom: Vec<ProjectionResponse>,
    pub meta: AgentContextMeta,
}

#[derive(sqlx::FromRow)]
struct ProjectionRow {
    id: Uuid,
    user_id: Uuid,
    projection_type: String,
    key: String,
    data: serde_json::Value,
    version: i64,
    last_event_id: Option<Uuid>,
    updated_at: DateTime<Utc>,
}

impl ProjectionRow {
    fn into_response(self) -> ProjectionResponse {
        let meta = ProjectionMeta {
            projection_version: self.version,
            computed_at: self.updated_at,
        };
        ProjectionResponse {
            projection: Projection {
                id: self.id,
                user_id: self.user_id,
                projection_type: self.projection_type,
                key: self.key,
                data: self.data,
                version: self.version,
                last_event_id: self.last_event_id,
                updated_at: self.updated_at,
            },
            meta,
        }
    }
}

#[derive(sqlx::FromRow)]
struct SystemConfigRow {
    data: serde_json::Value,
    version: i64,
    updated_at: DateTime<Utc>,
}

fn clamp_limit(value: Option<i64>, default: i64, max: i64) -> i64 {
    value.unwrap_or(default).max(1).min(max)
}

fn bootstrap_user_profile(user_id: Uuid) -> ProjectionResponse {
    let now = Utc::now();
    ProjectionResponse {
        projection: Projection {
            id: Uuid::nil(),
            user_id,
            projection_type: "user_profile".to_string(),
            key: "me".to_string(),
            data: serde_json::json!({
                "user": null,
                "agenda": [{
                    "priority": "high",
                    "type": "onboarding_needed",
                    "detail": "New user. No data yet. Produce initial events to bootstrap profile.",
                    "dimensions": ["user_profile"]
                }]
            }),
            version: 0,
            last_event_id: None,
            updated_at: now,
        },
        meta: ProjectionMeta {
            projection_version: 0,
            computed_at: now,
        },
    }
}

async fn fetch_projection(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
    projection_type: &str,
    key: &str,
) -> Result<Option<ProjectionResponse>, AppError> {
    let row = sqlx::query_as::<_, ProjectionRow>(
        r#"
        SELECT id, user_id, projection_type, key, data, version, last_event_id, updated_at
        FROM projections
        WHERE user_id = $1 AND projection_type = $2 AND key = $3
        "#,
    )
    .bind(user_id)
    .bind(projection_type)
    .bind(key)
    .fetch_optional(&mut **tx)
    .await?;

    Ok(row.map(|r| r.into_response()))
}

async fn fetch_projection_list(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
    projection_type: &str,
    limit: i64,
) -> Result<Vec<ProjectionResponse>, AppError> {
    let rows = sqlx::query_as::<_, ProjectionRow>(
        r#"
        SELECT id, user_id, projection_type, key, data, version, last_event_id, updated_at
        FROM projections
        WHERE user_id = $1 AND projection_type = $2
        ORDER BY updated_at DESC, key ASC
        LIMIT $3
        "#,
    )
    .bind(user_id)
    .bind(projection_type)
    .bind(limit)
    .fetch_all(&mut **tx)
    .await?;

    Ok(rows.into_iter().map(|r| r.into_response()).collect())
}

/// Get agent context bundle in a single read call.
///
/// Returns the deployment-static system config, user profile, and key
/// projections that agents typically need to answer user requests.
#[utoipa::path(
    get,
    path = "/v1/agent/context",
    params(AgentContextParams),
    responses(
        (status = 200, description = "Agent context bundle", body = AgentContextResponse),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_agent_context(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Query(params): Query<AgentContextParams>,
) -> Result<Json<AgentContextResponse>, AppError> {
    let user_id = auth.user_id;
    let exercise_limit = clamp_limit(params.exercise_limit, 5, 100);
    let custom_limit = clamp_limit(params.custom_limit, 10, 100);

    let mut tx = state.db.begin().await?;

    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let system = sqlx::query_as::<_, SystemConfigRow>(
        "SELECT data, version, updated_at FROM system_config WHERE key = 'global'",
    )
    .fetch_optional(&mut *tx)
    .await?
    .map(|row| SystemConfigResponse {
        data: row.data,
        version: row.version,
        updated_at: row.updated_at,
    });

    let user_profile = fetch_projection(&mut tx, user_id, "user_profile", "me")
        .await?
        .unwrap_or_else(|| bootstrap_user_profile(user_id));

    let training_timeline = fetch_projection(&mut tx, user_id, "training_timeline", "overview").await?;
    let body_composition = fetch_projection(&mut tx, user_id, "body_composition", "overview").await?;
    let recovery = fetch_projection(&mut tx, user_id, "recovery", "overview").await?;
    let nutrition = fetch_projection(&mut tx, user_id, "nutrition", "overview").await?;
    let training_plan = fetch_projection(&mut tx, user_id, "training_plan", "overview").await?;

    let exercise_progression =
        fetch_projection_list(&mut tx, user_id, "exercise_progression", exercise_limit).await?;
    let custom = fetch_projection_list(&mut tx, user_id, "custom", custom_limit).await?;

    tx.commit().await?;

    Ok(Json(AgentContextResponse {
        system,
        user_profile,
        training_timeline,
        body_composition,
        recovery,
        nutrition,
        training_plan,
        exercise_progression,
        custom,
        meta: AgentContextMeta {
            generated_at: Utc::now(),
            exercise_limit,
            custom_limit,
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::{bootstrap_user_profile, clamp_limit};
    use uuid::Uuid;

    #[test]
    fn clamp_limit_enforces_defaults_and_bounds() {
        assert_eq!(clamp_limit(None, 5, 100), 5);
        assert_eq!(clamp_limit(Some(0), 5, 100), 1);
        assert_eq!(clamp_limit(Some(101), 5, 100), 100);
        assert_eq!(clamp_limit(Some(7), 5, 100), 7);
    }

    #[test]
    fn bootstrap_user_profile_contains_onboarding_agenda() {
        let user_id = Uuid::now_v7();
        let response = bootstrap_user_profile(user_id);
        assert_eq!(response.projection.user_id, user_id);
        assert_eq!(response.projection.projection_type, "user_profile");
        assert_eq!(response.projection.key, "me");

        let agenda = response.projection.data["agenda"].as_array().unwrap();
        assert!(!agenda.is_empty());
        assert_eq!(agenda[0]["type"], "onboarding_needed");
    }
}
