use axum::extract::{Path, State};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use kura_core::error::ApiError;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/imports/jobs", post(create_import_job))
        .route("/v1/imports/jobs/{job_id}", get(get_import_job))
}

#[derive(Debug, Deserialize, Serialize, utoipa::ToSchema)]
pub struct CreateImportJobRequest {
    pub provider: String,
    pub provider_user_id: String,
    pub file_format: String,
    pub payload_text: String,
    pub external_activity_id: String,
    #[serde(default)]
    pub external_event_version: Option<String>,
    #[serde(default)]
    pub raw_payload_ref: Option<String>,
    #[serde(default)]
    pub ingestion_method: Option<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct CreateImportJobResponse {
    pub job_id: Uuid,
    pub status: String,
    pub queued_at: DateTime<Utc>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct ImportJobStatusResponse {
    pub job_id: Uuid,
    pub status: String,
    pub provider: String,
    pub file_format: String,
    pub external_activity_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub external_event_version: Option<String>,
    pub receipt: serde_json::Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_message: Option<String>,
    pub created_at: DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub started_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, sqlx::FromRow)]
struct ImportJobRow {
    id: Uuid,
    status: String,
    provider: String,
    file_format: String,
    external_activity_id: String,
    external_event_version: Option<String>,
    receipt: serde_json::Value,
    error_code: Option<String>,
    error_message: Option<String>,
    created_at: DateTime<Utc>,
    started_at: Option<DateTime<Utc>>,
    completed_at: Option<DateTime<Utc>>,
}

impl ImportJobRow {
    fn into_response(self) -> ImportJobStatusResponse {
        ImportJobStatusResponse {
            job_id: self.id,
            status: self.status,
            provider: self.provider,
            file_format: self.file_format,
            external_activity_id: self.external_activity_id,
            external_event_version: self.external_event_version,
            receipt: self.receipt,
            error_code: self.error_code,
            error_message: self.error_message,
            created_at: self.created_at,
            started_at: self.started_at,
            completed_at: self.completed_at,
        }
    }
}

fn normalized_non_empty(value: &str, field: &str) -> Result<String, AppError> {
    let normalized = value.trim().to_lowercase();
    if normalized.is_empty() {
        return Err(AppError::Validation {
            message: format!("{field} must not be empty"),
            field: Some(field.to_string()),
            received: Some(serde_json::Value::String(value.to_string())),
            docs_hint: None,
        });
    }
    Ok(normalized)
}

fn validate_provider(provider: &str) -> Result<String, AppError> {
    let normalized = normalized_non_empty(provider, "provider")?;
    match normalized.as_str() {
        "garmin" | "strava" | "trainingpeaks" => Ok(normalized),
        _ => Err(AppError::Validation {
            message: "provider must be one of garmin, strava, trainingpeaks".to_string(),
            field: Some("provider".to_string()),
            received: Some(serde_json::Value::String(provider.to_string())),
            docs_hint: Some("Use provider identifiers supported by mapping matrix v1.".to_string()),
        }),
    }
}

fn validate_file_format(file_format: &str) -> Result<String, AppError> {
    let normalized = normalized_non_empty(file_format, "file_format")?;
    match normalized.as_str() {
        "fit" | "tcx" | "gpx" => Ok(normalized),
        _ => Err(AppError::Validation {
            message: "file_format must be one of fit, tcx, gpx".to_string(),
            field: Some("file_format".to_string()),
            received: Some(serde_json::Value::String(file_format.to_string())),
            docs_hint: Some("Supported import formats: fit, tcx, gpx.".to_string()),
        }),
    }
}

fn validate_ingestion_method(value: Option<&str>) -> Result<String, AppError> {
    let normalized = value.unwrap_or("file_import").trim().to_lowercase();
    match normalized.as_str() {
        "file_import" | "connector_api" | "manual_backfill" => Ok(normalized),
        _ => Err(AppError::Validation {
            message: "ingestion_method must be file_import, connector_api, or manual_backfill"
                .to_string(),
            field: Some("ingestion_method".to_string()),
            received: Some(serde_json::Value::String(normalized)),
            docs_hint: Some("For launch import use file_import.".to_string()),
        }),
    }
}

/// Queue a new async external import job (FIT/TCX/GPX).
#[utoipa::path(
    post,
    path = "/v1/imports/jobs",
    request_body = CreateImportJobRequest,
    responses(
        (status = 200, description = "Import job queued", body = CreateImportJobResponse),
        (status = 400, description = "Validation failed", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "imports"
)]
pub async fn create_import_job(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<CreateImportJobRequest>,
) -> Result<Json<CreateImportJobResponse>, AppError> {
    let user_id = auth.user_id;
    let provider = validate_provider(&req.provider)?;
    let file_format = validate_file_format(&req.file_format)?;
    let ingestion_method = validate_ingestion_method(req.ingestion_method.as_deref())?;
    let provider_user_id = req.provider_user_id.trim();
    if provider_user_id.is_empty() {
        return Err(AppError::Validation {
            message: "provider_user_id must not be empty".to_string(),
            field: Some("provider_user_id".to_string()),
            received: None,
            docs_hint: None,
        });
    }
    let external_activity_id = req.external_activity_id.trim();
    if external_activity_id.is_empty() {
        return Err(AppError::Validation {
            message: "external_activity_id must not be empty".to_string(),
            field: Some("external_activity_id".to_string()),
            received: None,
            docs_hint: None,
        });
    }
    let payload_text = req.payload_text.trim();
    if payload_text.is_empty() {
        return Err(AppError::Validation {
            message: "payload_text must not be empty".to_string(),
            field: Some("payload_text".to_string()),
            received: None,
            docs_hint: Some("Send file content as text payload for import processing.".to_string()),
        });
    }

    let job_id = Uuid::now_v7();
    let queued_at = Utc::now();

    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    sqlx::query(
        r#"
        INSERT INTO external_import_jobs (
            id,
            user_id,
            provider,
            provider_user_id,
            file_format,
            ingestion_method,
            external_activity_id,
            external_event_version,
            raw_payload_ref,
            payload_text,
            status,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'queued', $11)
        "#,
    )
    .bind(job_id)
    .bind(user_id)
    .bind(&provider)
    .bind(provider_user_id)
    .bind(&file_format)
    .bind(&ingestion_method)
    .bind(external_activity_id)
    .bind(req.external_event_version.as_deref())
    .bind(req.raw_payload_ref.as_deref())
    .bind(payload_text)
    .bind(queued_at)
    .execute(&mut *tx)
    .await?;

    sqlx::query(
        r#"
        INSERT INTO background_jobs (user_id, job_type, payload, max_retries)
        VALUES ($1, 'external_import.process', $2, 3)
        "#,
    )
    .bind(user_id)
    .bind(json!({
        "import_job_id": job_id.to_string(),
        "user_id": user_id.to_string(),
    }))
    .execute(&mut *tx)
    .await?;

    tx.commit().await?;

    Ok(Json(CreateImportJobResponse {
        job_id,
        status: "queued".to_string(),
        queued_at,
    }))
}

/// Fetch import job status + receipt.
#[utoipa::path(
    get,
    path = "/v1/imports/jobs/{job_id}",
    params(
        ("job_id" = Uuid, Path, description = "Import job id")
    ),
    responses(
        (status = 200, description = "Import job status", body = ImportJobStatusResponse),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 404, description = "Job not found", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "imports"
)]
pub async fn get_import_job(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path(job_id): Path<Uuid>,
) -> Result<Json<ImportJobStatusResponse>, AppError> {
    let user_id = auth.user_id;

    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let row = sqlx::query_as::<_, ImportJobRow>(
        r#"
        SELECT
            id,
            status,
            provider,
            file_format,
            external_activity_id,
            external_event_version,
            receipt,
            error_code,
            error_message,
            created_at,
            started_at,
            completed_at
        FROM external_import_jobs
        WHERE id = $1
          AND user_id = $2
        "#,
    )
    .bind(job_id)
    .bind(user_id)
    .fetch_optional(&mut *tx)
    .await?;

    tx.commit().await?;

    match row {
        Some(job) => Ok(Json(job.into_response())),
        None => Err(AppError::NotFound {
            resource: format!("import job {}", job_id),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::{AppError, validate_file_format, validate_ingestion_method, validate_provider};

    #[test]
    fn provider_validation_accepts_supported_values() {
        assert_eq!(validate_provider("garmin").unwrap(), "garmin");
        assert_eq!(validate_provider("STRAVA").unwrap(), "strava");
        assert_eq!(validate_provider("trainingpeaks").unwrap(), "trainingpeaks");
    }

    #[test]
    fn provider_validation_rejects_unknown_values() {
        let err = validate_provider("polar").expect_err("unsupported provider must fail");
        match err {
            AppError::Validation { field, .. } => assert_eq!(field.as_deref(), Some("provider")),
            other => panic!("unexpected error: {:?}", other),
        }
    }

    #[test]
    fn file_format_validation_accepts_fit_tcx_gpx() {
        assert_eq!(validate_file_format("fit").unwrap(), "fit");
        assert_eq!(validate_file_format("TCX").unwrap(), "tcx");
        assert_eq!(validate_file_format("gpx").unwrap(), "gpx");
    }

    #[test]
    fn file_format_validation_rejects_unknown_formats() {
        let err = validate_file_format("csv").expect_err("unknown format must fail");
        match err {
            AppError::Validation { field, .. } => {
                assert_eq!(field.as_deref(), Some("file_format"));
            }
            other => panic!("unexpected error: {:?}", other),
        }
    }

    #[test]
    fn ingestion_method_validation_defaults_to_file_import() {
        assert_eq!(
            validate_ingestion_method(None).unwrap(),
            "file_import".to_string()
        );
        assert_eq!(
            validate_ingestion_method(Some("connector_api")).unwrap(),
            "connector_api".to_string()
        );
    }
}
