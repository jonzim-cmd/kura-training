use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;
use uuid::Uuid;

/// A pre-computed read model derived from events.
/// Agents read projections, never the event store directly.
#[derive(Debug, Serialize, Deserialize, ToSchema)]
pub struct Projection {
    pub id: Uuid,
    pub user_id: Uuid,
    pub projection_type: String,
    pub key: String,
    pub data: serde_json::Value,
    pub version: i64,
    pub last_event_id: Option<Uuid>,
    pub updated_at: DateTime<Utc>,
}

/// Response wrapper for a single projection
#[derive(Debug, Serialize, ToSchema)]
pub struct ProjectionResponse {
    #[serde(flatten)]
    pub projection: Projection,
    pub meta: ProjectionMeta,
}

/// Metadata about the projection computation
#[derive(Debug, Serialize, ToSchema)]
pub struct ProjectionMeta {
    /// How many times this projection has been recomputed
    pub projection_version: i64,
    /// When the projection was last recomputed
    pub computed_at: DateTime<Utc>,
}
