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
    BatchCreateEventsRequest, CreateEventRequest, Event, EventMetadata, PaginatedResponse,
};

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/events", get(list_events).post(create_event))
        .route("/v1/events/batch", post(create_events_batch))
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

    Ok(())
}

/// Insert a single event into the database within a transaction that sets RLS context.
async fn insert_event(
    pool: &sqlx::PgPool,
    user_id: Uuid,
    req: CreateEventRequest,
) -> Result<Event, AppError> {
    let event_id = Uuid::now_v7();
    let metadata_json = serde_json::to_value(&req.metadata).map_err(|e| {
        AppError::Internal(format!("Failed to serialize metadata: {}", e))
    })?;

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

/// Create a single event
///
/// Accepts an event and stores it immutably. The event_type is free-form —
/// new types emerge from usage, not from a hardcoded schema.
#[utoipa::path(
    post,
    path = "/v1/events",
    request_body = CreateEventRequest,
    responses(
        (status = 201, description = "Event created", body = Event),
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

    let event = insert_event(&state.db, user_id, req).await?;

    Ok((StatusCode::CREATED, Json(event)))
}

/// Create multiple events atomically
///
/// All events in the batch are written in a single transaction.
/// If any event fails validation or conflicts, the entire batch is rolled back.
/// Use this for complete training sessions (session.started + sets + session.ended).
#[utoipa::path(
    post,
    path = "/v1/events/batch",
    request_body = BatchCreateEventsRequest,
    responses(
        (status = 201, description = "All events created", body = Vec<Event>),
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
    let user_id = auth.user_id;

    if req.events.is_empty() {
        return Err(AppError::Validation {
            message: "events array must not be empty".to_string(),
            field: Some("events".to_string()),
            received: None,
            docs_hint: Some("Provide at least one event in the batch".to_string()),
        });
    }

    if req.events.len() > 100 {
        return Err(AppError::Validation {
            message: format!("Batch size {} exceeds maximum of 100", req.events.len()),
            field: Some("events".to_string()),
            received: Some(serde_json::json!(req.events.len())),
            docs_hint: Some("Split large batches into chunks of 100 or fewer".to_string()),
        });
    }

    // Validate all events before writing any
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
            other => other,
        })?;
    }

    let mut tx = state.db.begin().await?;

    // Set RLS context for the entire batch transaction
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    // Prepare arrays for multi-row INSERT (avoids N+1 queries)
    let mut ids = Vec::with_capacity(req.events.len());
    let mut user_ids = Vec::with_capacity(req.events.len());
    let mut timestamps = Vec::with_capacity(req.events.len());
    let mut event_types = Vec::with_capacity(req.events.len());
    let mut data_values = Vec::with_capacity(req.events.len());
    let mut metadata_values = Vec::with_capacity(req.events.len());
    let mut idempotency_keys = Vec::with_capacity(req.events.len());

    for event_req in &req.events {
        ids.push(Uuid::now_v7());
        user_ids.push(user_id);
        timestamps.push(event_req.timestamp);
        event_types.push(event_req.event_type.clone());
        data_values.push(event_req.data.clone());
        metadata_values.push(serde_json::to_value(&event_req.metadata).map_err(|e| {
            AppError::Internal(format!("Failed to serialize metadata: {}", e))
        })?);
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
                let key = idempotency_keys.iter()
                    .find(|k| search_text.contains(k.as_str()))
                    .cloned()
                    .unwrap_or_else(|| "unknown".to_string());
                return AppError::IdempotencyConflict { idempotency_key: key };
            }
        }
        AppError::Database(e)
    })?;

    tx.commit().await?;

    let created_events: Vec<Event> = rows.into_iter().map(|r| r.into_event()).collect();

    Ok((StatusCode::CREATED, Json(created_events)))
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
