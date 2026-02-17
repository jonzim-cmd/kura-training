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

const ANALYSIS_REQUEST_SCHEMA_VERSION: &str = "analysis_request.v1";
const ANALYSIS_RESULT_SCHEMA_VERSION: &str = "deep_analysis_result.v1";

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/analysis/jobs", post(create_analysis_job))
        .route("/v1/analysis/jobs/{job_id}", get(get_analysis_job))
}

#[derive(Debug, Deserialize, Serialize, utoipa::ToSchema)]
pub struct CreateAnalysisJobRequest {
    pub objective: String,
    #[serde(default)]
    pub horizon_days: Option<i32>,
    #[serde(default)]
    pub focus: Vec<String>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct CreateAnalysisJobResponse {
    pub job_id: Uuid,
    pub status: String,
    pub queued_at: DateTime<Utc>,
}

#[derive(Debug, Serialize, utoipa::ToSchema)]
pub struct AnalysisJobStatusResponse {
    pub job_id: Uuid,
    pub status: String,
    pub objective: String,
    pub horizon_days: i32,
    pub focus: Vec<String>,
    pub request_payload: serde_json::Value,
    pub result_payload: serde_json::Value,
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
struct AnalysisJobRow {
    id: Uuid,
    status: String,
    objective: String,
    horizon_days: i32,
    focus: serde_json::Value,
    request_payload: serde_json::Value,
    result_payload: serde_json::Value,
    error_code: Option<String>,
    error_message: Option<String>,
    created_at: DateTime<Utc>,
    started_at: Option<DateTime<Utc>>,
    completed_at: Option<DateTime<Utc>>,
}

impl AnalysisJobRow {
    fn into_response(self) -> AnalysisJobStatusResponse {
        AnalysisJobStatusResponse {
            job_id: self.id,
            status: self.status,
            objective: self.objective,
            horizon_days: self.horizon_days,
            focus: parse_focus_array(&self.focus),
            request_payload: self.request_payload,
            result_payload: self.result_payload,
            error_code: self.error_code,
            error_message: self.error_message,
            created_at: self.created_at,
            started_at: self.started_at,
            completed_at: self.completed_at,
        }
    }
}

fn parse_focus_array(value: &serde_json::Value) -> Vec<String> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str())
                .map(ToString::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn validate_objective(raw: &str) -> Result<String, AppError> {
    let objective = raw.trim();
    if objective.is_empty() {
        return Err(AppError::Validation {
            message: "objective must not be empty".to_string(),
            field: Some("objective".to_string()),
            received: Some(serde_json::Value::String(raw.to_string())),
            docs_hint: Some("Provide a concrete analysis objective.".to_string()),
        });
    }
    if objective.len() > 1_000 {
        return Err(AppError::Validation {
            message: "objective must be <= 1000 characters".to_string(),
            field: Some("objective".to_string()),
            received: Some(serde_json::Value::String(raw.to_string())),
            docs_hint: None,
        });
    }
    Ok(objective.to_string())
}

fn validate_horizon_days(raw: Option<i32>) -> Result<i32, AppError> {
    let horizon_days = raw.unwrap_or(90);
    if !(1..=3650).contains(&horizon_days) {
        return Err(AppError::Validation {
            message: "horizon_days must be between 1 and 3650".to_string(),
            field: Some("horizon_days".to_string()),
            received: Some(json!(horizon_days)),
            docs_hint: Some("Use a positive day-window for analysis.".to_string()),
        });
    }
    Ok(horizon_days)
}

fn normalize_focus(values: Vec<String>) -> Vec<String> {
    let mut out = Vec::<String>::new();
    for raw in values {
        let normalized = raw.trim().to_lowercase();
        if normalized.is_empty() {
            continue;
        }
        if out.iter().any(|item| item == &normalized) {
            continue;
        }
        out.push(normalized);
    }
    out
}

/// Queue a new async deep-analysis job for the authenticated user.
#[utoipa::path(
    post,
    path = "/v1/analysis/jobs",
    request_body = CreateAnalysisJobRequest,
    responses(
        (status = 200, description = "Analysis job queued", body = CreateAnalysisJobResponse),
        (status = 400, description = "Validation failed", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "analysis"
)]
pub async fn create_analysis_job(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<CreateAnalysisJobRequest>,
) -> Result<Json<CreateAnalysisJobResponse>, AppError> {
    let user_id = auth.user_id;
    let objective = validate_objective(&req.objective)?;
    let horizon_days = validate_horizon_days(req.horizon_days)?;
    let focus = normalize_focus(req.focus);

    let job_id = Uuid::now_v7();
    let queued_at = Utc::now();
    let request_payload = json!({
        "schema_version": ANALYSIS_REQUEST_SCHEMA_VERSION,
        "objective": objective,
        "horizon_days": horizon_days,
        "focus": focus,
        "requested_at": queued_at,
    });

    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    sqlx::query(
        r#"
        INSERT INTO analysis_jobs (
            id,
            user_id,
            status,
            objective,
            horizon_days,
            focus,
            request_payload,
            result_payload,
            created_at
        )
        VALUES ($1, $2, 'queued', $3, $4, $5, $6, '{}'::jsonb, $7)
        "#,
    )
    .bind(job_id)
    .bind(user_id)
    .bind(&objective)
    .bind(horizon_days)
    .bind(json!(focus))
    .bind(&request_payload)
    .bind(queued_at)
    .execute(&mut *tx)
    .await?;

    sqlx::query(
        r#"
        INSERT INTO background_jobs (user_id, job_type, payload, max_retries)
        VALUES ($1, 'analysis.deep_insight', $2, 3)
        "#,
    )
    .bind(user_id)
    .bind(json!({
        "analysis_job_id": job_id.to_string(),
        "user_id": user_id.to_string(),
        "result_schema_version": ANALYSIS_RESULT_SCHEMA_VERSION,
    }))
    .execute(&mut *tx)
    .await?;

    tx.commit().await?;

    Ok(Json(CreateAnalysisJobResponse {
        job_id,
        status: "queued".to_string(),
        queued_at,
    }))
}

/// Fetch analysis job status and result envelope.
#[utoipa::path(
    get,
    path = "/v1/analysis/jobs/{job_id}",
    params(
        ("job_id" = Uuid, Path, description = "Analysis job id")
    ),
    responses(
        (status = 200, description = "Analysis job status", body = AnalysisJobStatusResponse),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 404, description = "Job not found", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "analysis"
)]
pub async fn get_analysis_job(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Path(job_id): Path<Uuid>,
) -> Result<Json<AnalysisJobStatusResponse>, AppError> {
    let user_id = auth.user_id;

    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let row = sqlx::query_as::<_, AnalysisJobRow>(
        r#"
        SELECT
            id,
            status,
            objective,
            horizon_days,
            focus,
            request_payload,
            result_payload,
            error_code,
            error_message,
            created_at,
            started_at,
            completed_at
        FROM analysis_jobs
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
            resource: format!("analysis job {}", job_id),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        AppError, normalize_focus, parse_focus_array, validate_horizon_days, validate_objective,
    };
    use serde_json::json;

    #[test]
    fn objective_validation_rejects_empty_input() {
        let err = validate_objective("   ").expect_err("empty objective must fail");
        match err {
            AppError::Validation { field, .. } => assert_eq!(field.as_deref(), Some("objective")),
            other => panic!("unexpected error: {:?}", other),
        }
    }

    #[test]
    fn horizon_validation_defaults_and_bounds() {
        assert_eq!(validate_horizon_days(None).unwrap(), 90);
        assert_eq!(validate_horizon_days(Some(30)).unwrap(), 30);
        assert!(validate_horizon_days(Some(0)).is_err());
        assert!(validate_horizon_days(Some(5000)).is_err());
    }

    #[test]
    fn focus_normalization_trims_lowers_and_dedupes() {
        let normalized = normalize_focus(vec![
            "  Recovery ".to_string(),
            "".to_string(),
            "RECOVERY".to_string(),
            "sleep".to_string(),
        ]);
        assert_eq!(
            normalized,
            vec!["recovery".to_string(), "sleep".to_string()]
        );
    }

    #[test]
    fn focus_parser_returns_empty_for_non_array_values() {
        assert_eq!(
            parse_focus_array(&json!({"foo": "bar"})),
            Vec::<String>::new()
        );
        assert_eq!(
            parse_focus_array(&json!(["recovery", "sleep"])),
            vec!["recovery".to_string(), "sleep".to_string()]
        );
    }
}
