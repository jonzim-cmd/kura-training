use axum::extract::{Path, State};
use axum::routing::get;
use axum::{Json, Router};
use chrono::Utc;
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::projections::{Projection, ProjectionFreshness, ProjectionMeta, ProjectionResponse};

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/projections", get(snapshot))
        .route(
            "/v1/projections/{projection_type}/{key}",
            get(get_projection),
        )
        .route(
            "/v1/projections/{projection_type}",
            get(list_projections),
        )
}

/// Internal row type for sqlx mapping
#[derive(sqlx::FromRow)]
struct ProjectionRow {
    id: Uuid,
    user_id: Uuid,
    projection_type: String,
    key: String,
    data: serde_json::Value,
    version: i64,
    last_event_id: Option<Uuid>,
    updated_at: chrono::DateTime<chrono::Utc>,
}

impl ProjectionRow {
    fn into_response(self, now: chrono::DateTime<chrono::Utc>) -> ProjectionResponse {
        let computed_at = self.updated_at;
        let meta = ProjectionMeta {
            projection_version: self.version,
            computed_at,
            freshness: ProjectionFreshness::from_computed_at(computed_at, now),
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
                updated_at: computed_at,
            },
            meta,
        }
    }
}

/// Get all projections for the authenticated user (snapshot)
///
/// Returns every projection across all dimension types in a single call.
/// Designed for agent bootstrap: one request gives the full picture.
#[utoipa::path(
    get,
    path = "/v1/projections",
    responses(
        (status = 200, description = "All projections for this user", body = Vec<ProjectionResponse>),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "projections"
)]
pub async fn snapshot(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
) -> Result<Json<Vec<ProjectionResponse>>, AppError> {
    let user_id = auth.user_id;

    let mut tx = state.db.begin().await?;

    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let rows = sqlx::query_as::<_, ProjectionRow>(
        r#"
        SELECT id, user_id, projection_type, key, data, version, last_event_id, updated_at
        FROM projections
        WHERE user_id = $1
        ORDER BY projection_type, key
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut *tx)
    .await?;

    tx.commit().await?;

    let now = Utc::now();
    let responses: Vec<ProjectionResponse> = rows.into_iter().map(|r| r.into_response(now)).collect();
    Ok(Json(responses))
}

/// Get a single projection by type and key
///
/// Returns a pre-computed read model. Projections are updated asynchronously
/// when new events are written — there may be a brief delay after event creation.
#[utoipa::path(
    get,
    path = "/v1/projections/{projection_type}/{key}",
    params(
        ("projection_type" = String, Path, description = "Projection type (e.g. 'exercise_progression')"),
        ("key" = String, Path, description = "Projection key (e.g. 'squat')")
    ),
    responses(
        (status = 200, description = "Projection found", body = ProjectionResponse),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 404, description = "Projection not found", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "projections"
)]
pub async fn get_projection(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path((projection_type, key)): Path<(String, String)>,
) -> Result<Json<ProjectionResponse>, AppError> {
    let user_id = auth.user_id;

    let mut tx = state.db.begin().await?;

    // Set RLS context
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let row = sqlx::query_as::<_, ProjectionRow>(
        r#"
        SELECT id, user_id, projection_type, key, data, version, last_event_id, updated_at
        FROM projections
        WHERE user_id = $1 AND projection_type = $2 AND key = $3
        "#,
    )
    .bind(user_id)
    .bind(&projection_type)
    .bind(&key)
    .fetch_optional(&mut *tx)
    .await?;

    tx.commit().await?;

    let now = Utc::now();
    match row {
        Some(r) => Ok(Json(r.into_response(now))),
        // Bootstrap response for new users: return empty profile with
        // onboarding_needed agenda instead of 404 (Decision 8).
        // The full three-layer response becomes available after the first event
        // triggers the Python worker.
        None if projection_type == "user_profile" && key == "me" => {
            let computed_at = now;
            Ok(Json(ProjectionResponse {
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
                    updated_at: computed_at,
                },
                meta: ProjectionMeta {
                    projection_version: 0,
                    computed_at,
                    freshness: ProjectionFreshness::from_computed_at(computed_at, now),
                },
            }))
        }
        None => Err(AppError::NotFound {
            resource: format!("projection {}/{}", projection_type, key),
        }),
    }
}

/// List all projections of a given type for the authenticated user
///
/// Returns all projections of the specified type. For example,
/// listing all 'exercise_progression' projections returns one entry per exercise.
#[utoipa::path(
    get,
    path = "/v1/projections/{projection_type}",
    params(
        ("projection_type" = String, Path, description = "Projection type (e.g. 'exercise_progression')")
    ),
    responses(
        (status = 200, description = "List of projections", body = Vec<ProjectionResponse>),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "projections"
)]
pub async fn list_projections(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path(projection_type): Path<String>,
) -> Result<Json<Vec<ProjectionResponse>>, AppError> {
    let user_id = auth.user_id;

    let mut tx = state.db.begin().await?;

    // Set RLS context
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let rows = sqlx::query_as::<_, ProjectionRow>(
        r#"
        SELECT id, user_id, projection_type, key, data, version, last_event_id, updated_at
        FROM projections
        WHERE user_id = $1 AND projection_type = $2
        ORDER BY key
        "#,
    )
    .bind(user_id)
    .bind(&projection_type)
    .fetch_all(&mut *tx)
    .await?;

    tx.commit().await?;

    let now = Utc::now();
    let responses: Vec<ProjectionResponse> = rows.into_iter().map(|r| r.into_response(now)).collect();
    Ok(Json(responses))
}
