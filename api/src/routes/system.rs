use axum::extract::{Query, State};
use axum::routing::get;
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use kura_core::error::ApiError;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

pub const SYSTEM_CONFIG_MANIFEST_SCHEMA_VERSION: &str = "system_config_manifest.v1";
pub const SYSTEM_CONFIG_SECTION_SCHEMA_VERSION: &str = "system_config_section.v1";
const SYSTEM_CONFIG_SECTION_QUERY_ENDPOINT: &str = "/v1/system/config/section";
const SYSTEM_CONFIG_MANIFEST_RESOURCE_URI: &str = "kura://system/config/manifest";

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/v1/system/config", get(get_system_config))
        .route(
            "/v1/system/config/manifest",
            get(get_system_config_manifest),
        )
        .route("/v1/system/config/section", get(get_system_config_section))
}

/// Response for GET /v1/system/config
#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SystemConfigResponse {
    pub data: serde_json::Value,
    pub version: i64,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SystemConfigSectionFetchContract {
    pub method: String,
    pub path: String,
    pub query: String,
    pub resource_uri: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SystemConfigSectionManifestItem {
    pub section: String,
    pub purpose: String,
    /// core | extended
    pub criticality: String,
    pub approx_bytes: usize,
    pub approx_tokens: usize,
    pub fetch: SystemConfigSectionFetchContract,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SystemConfigManifestResponse {
    pub schema_version: String,
    pub handle: String,
    pub version: i64,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub sections: Vec<SystemConfigSectionManifestItem>,
}

#[derive(Debug, Deserialize, utoipa::IntoParams)]
pub struct SystemConfigSectionQuery {
    /// Section id from manifest, e.g. `system_config.event_conventions` or
    /// `system_config.event_conventions::set.logged`.
    pub section: String,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SystemConfigSectionResponse {
    pub schema_version: String,
    pub handle: String,
    pub section: String,
    pub version: i64,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub value: serde_json::Value,
}

/// Internal row type for sqlx mapping
#[derive(sqlx::FromRow)]
struct SystemConfigRow {
    data: serde_json::Value,
    version: i64,
    updated_at: chrono::DateTime<chrono::Utc>,
}

/// Get deployment-static system configuration
///
/// Returns dimensions, event conventions, interview guide, and normalization
/// conventions. This data is identical for all users and changes only on
/// code deployment. Agents should cache this per session.
#[utoipa::path(
    get,
    path = "/v1/system/config",
    responses(
        (status = 200, description = "System configuration", body = SystemConfigResponse),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 404, description = "System config not yet available (worker has not started)")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_system_config(
    State(state): State<AppState>,
    _auth: AuthenticatedUser,
) -> Result<Json<SystemConfigResponse>, AppError> {
    let row = fetch_system_config_row(&state).await?;

    match row {
        Some(r) => Ok(Json(SystemConfigResponse {
            data: r.data,
            version: r.version,
            updated_at: r.updated_at,
        })),
        None => Err(AppError::NotFound {
            resource: "system_config/global".to_string(),
        }),
    }
}

/// Get machine-readable section manifest for deployment-static system config.
///
/// Returns a complete index of section ids with fetch contracts and size hints.
#[utoipa::path(
    get,
    path = "/v1/system/config/manifest",
    responses(
        (status = 200, description = "System config section manifest", body = SystemConfigManifestResponse),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 404, description = "System config not yet available (worker has not started)")
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_system_config_manifest(
    State(state): State<AppState>,
    _auth: AuthenticatedUser,
) -> Result<Json<SystemConfigManifestResponse>, AppError> {
    let row = fetch_system_config_row(&state).await?;
    match row {
        Some(r) => {
            let system = SystemConfigResponse {
                data: r.data,
                version: r.version,
                updated_at: r.updated_at,
            };
            Ok(Json(build_system_config_manifest(&system)))
        }
        None => Err(AppError::NotFound {
            resource: "system_config/global".to_string(),
        }),
    }
}

/// Get one section from deployment-static system config by section id.
///
/// Use section ids from `/v1/system/config/manifest`.
#[utoipa::path(
    get,
    path = "/v1/system/config/section",
    params(SystemConfigSectionQuery),
    responses(
        (status = 200, description = "One system config section", body = SystemConfigSectionResponse),
        (status = 400, description = "Validation failed", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError),
        (status = 404, description = "System config or section not found", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "system"
)]
pub async fn get_system_config_section(
    State(state): State<AppState>,
    _auth: AuthenticatedUser,
    Query(query): Query<SystemConfigSectionQuery>,
) -> Result<Json<SystemConfigSectionResponse>, AppError> {
    let section = query.section.trim();
    if section.is_empty() {
        return Err(AppError::Validation {
            message: "section is required".to_string(),
            field: Some("section".to_string()),
            received: Some(Value::String(query.section)),
            docs_hint: Some(
                "Use a section id from GET /v1/system/config/manifest (e.g. system_config.event_conventions)."
                    .to_string(),
            ),
        });
    }

    let row = fetch_system_config_row(&state).await?;
    let Some(row) = row else {
        return Err(AppError::NotFound {
            resource: "system_config/global".to_string(),
        });
    };

    let system = SystemConfigResponse {
        data: row.data,
        version: row.version,
        updated_at: row.updated_at,
    };
    let Some(value) = resolve_system_config_section_value(&system.data, section) else {
        return Err(AppError::NotFound {
            resource: format!("system_config/section/{section}"),
        });
    };

    Ok(Json(SystemConfigSectionResponse {
        schema_version: SYSTEM_CONFIG_SECTION_SCHEMA_VERSION.to_string(),
        handle: build_system_config_handle(system.version),
        section: section.to_string(),
        version: system.version,
        updated_at: system.updated_at,
        value,
    }))
}

pub(crate) fn build_system_config_handle(version: i64) -> String {
    format!("system_config/global@v{version}")
}

pub(crate) fn build_system_config_manifest(
    system: &SystemConfigResponse,
) -> SystemConfigManifestResponse {
    SystemConfigManifestResponse {
        schema_version: SYSTEM_CONFIG_MANIFEST_SCHEMA_VERSION.to_string(),
        handle: build_system_config_handle(system.version),
        version: system.version,
        updated_at: system.updated_at,
        sections: build_system_config_manifest_sections(&system.data),
    }
}

pub(crate) fn build_system_config_manifest_sections(
    data: &Value,
) -> Vec<SystemConfigSectionManifestItem> {
    let mut sections = Vec::new();

    sections.push(section_manifest_item("system_config".to_string(), data));

    let Some(root) = data.as_object() else {
        return sections;
    };

    for key in root.keys() {
        let section = format!("system_config.{key}");
        if let Some(value) = resolve_system_config_section_value(data, &section) {
            sections.push(section_manifest_item(section, &value));
        }
    }

    for map_root in [
        "conventions",
        "event_conventions",
        "dimensions",
        "projection_schemas",
    ] {
        if let Some(entries) = root.get(map_root).and_then(Value::as_object) {
            for nested_key in entries.keys() {
                let section = format!("system_config.{map_root}::{nested_key}");
                if let Some(value) = resolve_system_config_section_value(data, &section) {
                    sections.push(section_manifest_item(section, &value));
                }
            }
        }
    }

    sections.sort_by(|a, b| a.section.cmp(&b.section));
    sections
}

pub(crate) fn resolve_system_config_section_value(data: &Value, section: &str) -> Option<Value> {
    let section = section.trim();
    if section == "system_config" {
        return Some(data.clone());
    }
    if !section.starts_with("system_config.") {
        return None;
    }

    let root = data.as_object()?;
    let rest = section.strip_prefix("system_config.")?;
    if let Some((root_key, nested_key)) = rest.split_once("::") {
        root.get(root_key)
            .and_then(Value::as_object)
            .and_then(|map| map.get(nested_key))
            .cloned()
    } else {
        root.get(rest).cloned()
    }
}

fn section_manifest_item(section: String, value: &Value) -> SystemConfigSectionManifestItem {
    let approx_bytes = serde_json::to_vec(value)
        .map(|bytes| bytes.len())
        .unwrap_or(0);
    let approx_tokens = approx_bytes.div_ceil(4);
    SystemConfigSectionManifestItem {
        purpose: section_purpose(&section),
        criticality: section_criticality(&section).to_string(),
        fetch: SystemConfigSectionFetchContract {
            method: "GET".to_string(),
            path: SYSTEM_CONFIG_SECTION_QUERY_ENDPOINT.to_string(),
            query: format!("section={section}"),
            resource_uri: SYSTEM_CONFIG_MANIFEST_RESOURCE_URI.to_string(),
        },
        section,
        approx_bytes,
        approx_tokens,
    }
}

fn section_criticality(section: &str) -> &'static str {
    match section {
        "system_config"
        | "system_config.event_conventions"
        | "system_config.operational_model"
        | "system_config.dimensions"
        | "system_config.projection_schemas"
        | "system_config.conventions::formal_event_type_policy_v1"
        | "system_config.conventions::write_preflight_v1" => "core",
        _ => "extended",
    }
}

fn section_purpose(section: &str) -> String {
    if section == "system_config" {
        return "Complete deployment-static system contract snapshot.".to_string();
    }
    if section.starts_with("system_config.event_conventions") {
        return "Formal event schema contract for writes and corrections.".to_string();
    }
    if section.starts_with("system_config.conventions") {
        return "Behavior/policy convention contract for agent operation.".to_string();
    }
    if section.starts_with("system_config.dimensions") {
        return "Projection dimension catalog and relationships.".to_string();
    }
    if section.starts_with("system_config.projection_schemas") {
        return "Expected projection output shapes.".to_string();
    }
    if section == "system_config.operational_model" {
        return "Event Sourcing paradigm and correction model (event.retracted, set.corrected)."
            .to_string();
    }
    "Deployment-static system section.".to_string()
}

async fn fetch_system_config_row(state: &AppState) -> Result<Option<SystemConfigRow>, AppError> {
    // No RLS context needed — system_config has no user_id.
    sqlx::query_as::<_, SystemConfigRow>(
        "SELECT data, version, updated_at FROM system_config WHERE key = 'global'",
    )
    .fetch_optional(&state.db)
    .await
    .map_err(AppError::from)
}

#[cfg(test)]
mod tests {
    use super::{build_system_config_manifest_sections, resolve_system_config_section_value};
    use serde_json::json;

    #[test]
    fn resolve_system_config_section_value_reads_root_and_nested_entries() {
        let data = json!({
            "event_conventions": {
                "set.logged": {"fields": {"reps": "number"}}
            },
            "conventions": {
                "write_preflight_v1": {"schema_version": "write_preflight.v1"}
            },
            "operational_model": {"paradigm": "Event Sourcing"}
        });

        assert_eq!(
            resolve_system_config_section_value(&data, "system_config")
                .expect("system root must resolve"),
            data
        );
        assert_eq!(
            resolve_system_config_section_value(&data, "system_config.operational_model")
                .expect("root section must resolve"),
            json!({"paradigm": "Event Sourcing"})
        );
        assert_eq!(
            resolve_system_config_section_value(
                &data,
                "system_config.event_conventions::set.logged"
            )
            .expect("nested event convention must resolve"),
            json!({"fields": {"reps": "number"}})
        );
        assert!(
            resolve_system_config_section_value(&data, "system_config.event_conventions::missing")
                .is_none()
        );
    }

    #[test]
    fn system_manifest_sections_include_nested_event_and_convention_entries() {
        let data = json!({
            "event_conventions": {
                "set.logged": {"fields": {"reps": "number"}},
                "event.retracted": {"fields": {"retracted_event_id": "string"}}
            },
            "conventions": {
                "write_preflight_v1": {"schema_version": "write_preflight.v1"}
            },
            "dimensions": {
                "training_timeline": {"description": "ok"}
            },
            "projection_schemas": {
                "user_profile": {"required": ["user"]}
            }
        });

        let sections = build_system_config_manifest_sections(&data);
        let ids: Vec<&str> = sections.iter().map(|item| item.section.as_str()).collect();
        assert!(ids.contains(&"system_config"));
        assert!(ids.contains(&"system_config.event_conventions"));
        assert!(ids.contains(&"system_config.event_conventions::set.logged"));
        assert!(ids.contains(&"system_config.event_conventions::event.retracted"));
        assert!(ids.contains(&"system_config.conventions::write_preflight_v1"));
        assert!(ids.contains(&"system_config.dimensions::training_timeline"));
        assert!(ids.contains(&"system_config.projection_schemas::user_profile"));
    }
}
