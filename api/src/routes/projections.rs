use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderValue, header::HeaderName};
use axum::response::IntoResponse;
use axum::routing::get;
use axum::{Json, Router};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::projections::{Projection, ProjectionFreshness, ProjectionMeta, ProjectionResponse};

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::privacy::get_or_create_analysis_subject_id;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/projections", get(snapshot))
        .route(
            "/v1/projections/{projection_type}/paged",
            get(list_projections_paged),
        )
        .route(
            "/v1/projections/{projection_type}/{key}",
            get(get_projection),
        )
        .route("/v1/projections/{projection_type}", get(list_projections))
}

#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct ListProjectionPageParams {
    /// Maximum number of projections to return (default 50, max 200)
    #[serde(default)]
    pub limit: Option<i64>,
    /// Cursor for pagination (opaque string from previous response's next_cursor)
    #[serde(default)]
    pub cursor: Option<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct PaginatedProjectionResponse {
    pub data: Vec<ProjectionResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_cursor: Option<String>,
    pub has_more: bool,
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
) -> Result<impl IntoResponse, AppError> {
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
    let responses: Vec<ProjectionResponse> =
        rows.into_iter().map(|r| r.into_response(now)).collect();
    let analysis_subject_id = get_or_create_analysis_subject_id(&state.db, user_id)
        .await
        .map_err(AppError::Database)?;
    let mut headers = HeaderMap::new();
    headers.insert(
        HeaderName::from_static("x-kura-analysis-subject"),
        HeaderValue::from_str(&analysis_subject_id)
            .map_err(|e| AppError::Internal(e.to_string()))?,
    );
    Ok((headers, Json(responses)))
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
) -> Result<impl IntoResponse, AppError> {
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

    let analysis_subject_id = get_or_create_analysis_subject_id(&state.db, user_id)
        .await
        .map_err(AppError::Database)?;
    let make_headers = || -> Result<HeaderMap, AppError> {
        let mut headers = HeaderMap::new();
        headers.insert(
            HeaderName::from_static("x-kura-analysis-subject"),
            HeaderValue::from_str(&analysis_subject_id)
                .map_err(|e| AppError::Internal(e.to_string()))?,
        );
        Ok(headers)
    };

    let now = Utc::now();
    match row {
        Some(r) => Ok((make_headers()?, Json(r.into_response(now))).into_response()),
        // Bootstrap response for new users: return empty profile with
        // onboarding_needed agenda instead of 404 (Decision 8).
        // The full three-layer response becomes available after the first event
        // triggers the Python worker.
        None if projection_type == "user_profile" && key == "me" => {
            let computed_at = now;
            Ok((make_headers()?, Json(ProjectionResponse {
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
            .into_response())
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
) -> Result<impl IntoResponse, AppError> {
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
    let responses: Vec<ProjectionResponse> =
        rows.into_iter().map(|r| r.into_response(now)).collect();
    let analysis_subject_id = get_or_create_analysis_subject_id(&state.db, user_id)
        .await
        .map_err(AppError::Database)?;
    let mut headers = HeaderMap::new();
    headers.insert(
        HeaderName::from_static("x-kura-analysis-subject"),
        HeaderValue::from_str(&analysis_subject_id)
            .map_err(|e| AppError::Internal(e.to_string()))?,
    );
    Ok((headers, Json(responses)))
}

/// List projections of a given type with cursor-based pagination.
///
/// Returns projections ordered by key ascending for deterministic iteration.
/// Use cursor-based pagination to reload large projection sets without
/// oversized payloads.
#[utoipa::path(
    get,
    path = "/v1/projections/{projection_type}/paged",
    params(
        ("projection_type" = String, Path, description = "Projection type (e.g. 'exercise_progression')"),
        ListProjectionPageParams
    ),
    responses(
        (status = 200, description = "Paginated projection list", body = PaginatedProjectionResponse),
        (status = 400, description = "Validation error", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "projections"
)]
pub async fn list_projections_paged(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path(projection_type): Path<String>,
    axum::extract::Query(params): axum::extract::Query<ListProjectionPageParams>,
) -> Result<impl IntoResponse, AppError> {
    let user_id = auth.user_id;
    let limit = params.limit.unwrap_or(50).clamp(1, 200);
    let fetch_limit = limit + 1;
    let cursor_key = match params.cursor.as_deref() {
        Some(raw) => Some(decode_projection_cursor(raw)?),
        None => None,
    };

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
        WHERE user_id = $1
          AND projection_type = $2
          AND ($3::text IS NULL OR key > $3)
        ORDER BY key ASC
        LIMIT $4
        "#,
    )
    .bind(user_id)
    .bind(&projection_type)
    .bind(cursor_key.as_deref())
    .bind(fetch_limit)
    .fetch_all(&mut *tx)
    .await?;

    tx.commit().await?;

    let has_more = rows.len() as i64 > limit;
    let now = Utc::now();
    let responses: Vec<ProjectionResponse> = rows
        .into_iter()
        .take(limit as usize)
        .map(|r| r.into_response(now))
        .collect();
    let next_cursor = if has_more {
        responses
            .last()
            .map(|item| encode_projection_cursor(&item.projection.key))
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
        Json(PaginatedProjectionResponse {
            data: responses,
            next_cursor,
            has_more,
        }),
    ))
}

fn encode_projection_cursor(key: &str) -> String {
    use base64::Engine;
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(key.as_bytes())
}

fn decode_projection_cursor(cursor: &str) -> Result<String, AppError> {
    use base64::Engine;
    let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(cursor)
        .map_err(|_| AppError::Validation {
            message: "Invalid cursor format".to_string(),
            field: Some("cursor".to_string()),
            received: Some(serde_json::Value::String(cursor.to_string())),
            docs_hint: Some("Use the next_cursor value from a previous response".to_string()),
        })?;
    let key = String::from_utf8(bytes).map_err(|_| AppError::Validation {
        message: "Invalid cursor encoding".to_string(),
        field: Some("cursor".to_string()),
        received: None,
        docs_hint: None,
    })?;
    if key.trim().is_empty() {
        return Err(AppError::Validation {
            message: "Invalid cursor payload".to_string(),
            field: Some("cursor".to_string()),
            received: None,
            docs_hint: Some("Use the next_cursor value from a previous response".to_string()),
        });
    }
    Ok(key)
}

#[cfg(test)]
mod tests {
    use super::{decode_projection_cursor, encode_projection_cursor};

    #[test]
    fn projection_cursor_roundtrip_preserves_key() {
        let key = "barbell_back_squat";
        let encoded = encode_projection_cursor(key);
        let decoded = decode_projection_cursor(&encoded).expect("cursor should decode");
        assert_eq!(decoded, key);
    }

    #[test]
    fn projection_cursor_rejects_invalid_payload() {
        let result = decode_projection_cursor("%%%");
        assert!(result.is_err());
    }
}
