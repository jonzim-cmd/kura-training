use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::post;
use axum::{Json, Router};
use uuid::Uuid;

use kura_core::error::ApiError;
use kura_core::events::{BatchCreateEventsRequest, CreateEventRequest, Event, EventMetadata};

use crate::error::AppError;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/events", post(create_event))
        .route("/v1/events/batch", post(create_events_batch))
}

/// Temporary: extract user_id from header until auth is implemented.
/// In production, this comes from the authenticated API key's associated user.
fn extract_user_id(headers: &HeaderMap) -> Result<Uuid, AppError> {
    let header_val = headers
        .get("x-user-id")
        .ok_or_else(|| AppError::Validation {
            message: "x-user-id header is required (temporary, will be replaced by auth)"
                .to_string(),
            field: Some("headers.x-user-id".to_string()),
            received: None,
            docs_hint: Some(
                "Pass x-user-id as a UUID header. This is temporary until API key auth is implemented."
                    .to_string(),
            ),
        })?;

    let user_id_str = header_val.to_str().map_err(|_| AppError::Validation {
        message: "x-user-id must be a valid UTF-8 string".to_string(),
        field: Some("headers.x-user-id".to_string()),
        received: None,
        docs_hint: None,
    })?;

    Uuid::parse_str(user_id_str).map_err(|_| AppError::Validation {
        message: "x-user-id must be a valid UUID".to_string(),
        field: Some("headers.x-user-id".to_string()),
        received: Some(serde_json::Value::String(user_id_str.to_string())),
        docs_hint: Some("Use a valid UUIDv4 or UUIDv7, e.g. 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'".to_string()),
    })
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
    sqlx::query(&format!(
        "SET LOCAL kura.current_user_id = '{}'",
        user_id
    ))
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
        (status = 409, description = "Idempotency conflict", body = ApiError)
    ),
    params(
        ("x-user-id" = Uuid, Header, description = "User ID (temporary, replaced by auth)")
    ),
    tag = "events"
)]
pub async fn create_event(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<CreateEventRequest>,
) -> Result<impl IntoResponse, AppError> {
    let user_id = extract_user_id(&headers)?;
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
        (status = 409, description = "Idempotency conflict", body = ApiError)
    ),
    params(
        ("x-user-id" = Uuid, Header, description = "User ID (temporary, replaced by auth)")
    ),
    tag = "events"
)]
pub async fn create_events_batch(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<BatchCreateEventsRequest>,
) -> Result<impl IntoResponse, AppError> {
    let user_id = extract_user_id(&headers)?;

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
    sqlx::query(&format!(
        "SET LOCAL kura.current_user_id = '{}'",
        user_id
    ))
    .execute(&mut *tx)
    .await?;

    let mut created_events = Vec::with_capacity(req.events.len());

    for (i, event_req) in req.events.into_iter().enumerate() {
        let event_id = Uuid::now_v7();
        let metadata_json = serde_json::to_value(&event_req.metadata).map_err(|e| {
            AppError::Internal(format!("Failed to serialize metadata: {}", e))
        })?;

        let row = sqlx::query_as::<_, EventRow>(
            r#"
            INSERT INTO events (id, user_id, timestamp, event_type, data, metadata)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, user_id, timestamp, event_type, data, metadata, created_at
            "#,
        )
        .bind(event_id)
        .bind(user_id)
        .bind(event_req.timestamp)
        .bind(&event_req.event_type)
        .bind(&event_req.data)
        .bind(&metadata_json)
        .fetch_one(&mut *tx)
        .await
        .map_err(|e| {
            if let sqlx::Error::Database(ref db_err) = e {
                if db_err.code().as_deref() == Some("23505") {
                    return AppError::IdempotencyConflict {
                        idempotency_key: event_req.metadata.idempotency_key.clone(),
                    };
                }
            }
            AppError::Database(e)
        })?;

        created_events.push(row.into_event());
    }

    tx.commit().await?;

    Ok((StatusCode::CREATED, Json(created_events)))
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
    created_at: chrono::DateTime<chrono::Utc>,
}

impl EventRow {
    fn into_event(self) -> Event {
        let metadata: EventMetadata =
            serde_json::from_value(self.metadata).unwrap_or_else(|_| EventMetadata {
                source: None,
                agent: None,
                device: None,
                session_id: None,
                idempotency_key: "unknown".to_string(),
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
