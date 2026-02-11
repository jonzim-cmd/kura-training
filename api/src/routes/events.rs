use std::collections::HashSet;

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
    CreateEventResponse, Event, EventMetadata, EventWarning, PaginatedResponse,
};

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub fn write_router() -> Router<AppState> {
    Router::new()
        .route("/v1/events", post(create_event))
        .route("/v1/events/batch", post(create_events_batch))
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

    Ok((StatusCode::CREATED, Json(CreateEventResponse { event, warnings })))
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

    // Fetch known exercise_ids once for the entire batch
    let mut known_ids = fetch_user_exercise_ids(&state.db, user_id).await?;

    // Validate all events before writing any
    let mut all_warnings: Vec<BatchEventWarning> = Vec::new();
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

    Ok((
        StatusCode::CREATED,
        Json(BatchCreateEventsResponse {
            events: created_events,
            warnings: all_warnings,
        }),
    ))
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
        let w = check_event_plausibility("meal.logged", &json!({
            "calories": 600, "protein_g": 40, "carbs_g": 70, "fat_g": 20
        }));
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
        let ids = known_ids(&["lateral_raise", "bench_press"]);
        let w = check_exercise_id_similarity(
            "set.logged",
            &json!({"exercise_id": "lu_raise"}),
            &ids,
        );
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
        let w = check_exercise_id_similarity(
            "set.logged",
            &json!({"exercise_id": ""}),
            &ids,
        );
        assert!(w.is_empty());
    }

    #[test]
    fn test_similarity_missing_exercise_id() {
        let ids = known_ids(&["bench_press"]);
        let w = check_exercise_id_similarity(
            "set.logged",
            &json!({"weight_kg": 80}),
            &ids,
        );
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
        let w = check_exercise_id_similarity(
            "set.logged",
            &json!({"exercise_id": "bench_pres"}),
            &ids,
        );
        assert_eq!(w.len(), 1);
        assert!(w[0].message.starts_with("New exercise_id 'bench_pres'. Similar existing:"));
        assert_eq!(w[0].severity, "warning");
    }
}
