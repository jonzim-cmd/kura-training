use axum::extract::{Path, State};
use axum::routing::get;
use axum::{Json, Router};
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::projections::{Projection, ProjectionMeta, ProjectionResponse};

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
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

    match row {
        Some(r) => Ok(Json(r.into_response())),
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

    let responses: Vec<ProjectionResponse> = rows.into_iter().map(|r| r.into_response()).collect();
    Ok(Json(responses))
}
