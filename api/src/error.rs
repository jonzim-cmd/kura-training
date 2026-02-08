use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use kura_core::error::{self, ApiError};

/// Internal error type that converts to structured API responses
#[derive(Debug)]
pub enum AppError {
    /// Validation error (400)
    Validation {
        message: String,
        field: Option<String>,
        received: Option<serde_json::Value>,
        docs_hint: Option<String>,
    },
    /// Idempotency conflict — same idempotency_key already used (409)
    IdempotencyConflict { idempotency_key: String },
    /// Database error (500)
    Database(sqlx::Error),
    /// Internal error (500)
    Internal(String),
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        // TODO: extract request_id from extensions once middleware is wired
        let request_id = uuid::Uuid::now_v7().to_string();

        let (status, api_error) = match self {
            AppError::Validation {
                message,
                field,
                received,
                docs_hint,
            } => (
                StatusCode::BAD_REQUEST,
                ApiError {
                    error: error::codes::VALIDATION_FAILED.to_string(),
                    message,
                    field,
                    received,
                    request_id,
                    docs_hint,
                },
            ),
            AppError::IdempotencyConflict { idempotency_key } => (
                StatusCode::CONFLICT,
                ApiError {
                    error: error::codes::IDEMPOTENCY_CONFLICT.to_string(),
                    message: format!(
                        "Event with idempotency_key '{}' already exists",
                        idempotency_key
                    ),
                    field: Some("metadata.idempotency_key".to_string()),
                    received: Some(serde_json::Value::String(idempotency_key)),
                    request_id,
                    docs_hint: Some(
                        "Each event must have a unique idempotency_key per user. \
                         If you're retrying a request, the original event was already created successfully."
                            .to_string(),
                    ),
                },
            ),
            AppError::Database(err) => {
                tracing::error!("Database error: {:?}", err);

                // Check if it's a unique constraint violation (idempotency)
                if let sqlx::Error::Database(ref db_err) = err {
                    if db_err.code().as_deref() == Some("23505") {
                        return AppError::IdempotencyConflict {
                            idempotency_key: "unknown".to_string(),
                        }
                        .into_response();
                    }
                }

                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    ApiError {
                        error: error::codes::INTERNAL_ERROR.to_string(),
                        message: "An internal error occurred".to_string(),
                        field: None,
                        received: None,
                        request_id,
                        docs_hint: None,
                    },
                )
            }
            AppError::Internal(msg) => {
                tracing::error!("Internal error: {}", msg);
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    ApiError {
                        error: error::codes::INTERNAL_ERROR.to_string(),
                        message: "An internal error occurred".to_string(),
                        field: None,
                        received: None,
                        request_id,
                        docs_hint: None,
                    },
                )
            }
        };

        (status, Json(api_error)).into_response()
    }
}

impl From<sqlx::Error> for AppError {
    fn from(err: sqlx::Error) -> Self {
        AppError::Database(err)
    }
}
