use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;
use uuid::Uuid;

pub const PROJECTION_WARMING_AFTER_SECONDS: i64 = 120;
pub const PROJECTION_STALE_AFTER_SECONDS: i64 = 900;

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
    /// Freshness SLA metadata for this projection
    pub freshness: ProjectionFreshness,
}

/// SLA status for projection freshness.
#[derive(Debug, Serialize, Deserialize, ToSchema, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProjectionFreshnessStatus {
    Fresh,
    Warming,
    Stale,
}

/// Freshness metadata derived from projection age.
#[derive(Debug, Serialize, Deserialize, ToSchema, Clone, Copy, PartialEq, Eq)]
pub struct ProjectionFreshness {
    /// Age of the projection in seconds, relative to request time.
    pub age_seconds: i64,
    /// Freshness bucket under current SLA thresholds.
    pub status: ProjectionFreshnessStatus,
    /// Threshold after which a projection is considered stale.
    pub stale_after_seconds: i64,
}

impl ProjectionFreshness {
    /// Build freshness metadata from `computed_at` and request time.
    pub fn from_computed_at(computed_at: DateTime<Utc>, now: DateTime<Utc>) -> Self {
        let age_seconds = now.signed_duration_since(computed_at).num_seconds().max(0);
        let status = if age_seconds <= PROJECTION_WARMING_AFTER_SECONDS {
            ProjectionFreshnessStatus::Fresh
        } else if age_seconds <= PROJECTION_STALE_AFTER_SECONDS {
            ProjectionFreshnessStatus::Warming
        } else {
            ProjectionFreshnessStatus::Stale
        };

        Self {
            age_seconds,
            status,
            stale_after_seconds: PROJECTION_STALE_AFTER_SECONDS,
        }
    }
}

#[cfg(test)]
mod tests {
    use chrono::{Duration, Utc};

    use super::{
        PROJECTION_STALE_AFTER_SECONDS, PROJECTION_WARMING_AFTER_SECONDS, ProjectionFreshness,
        ProjectionFreshnessStatus,
    };

    #[test]
    fn freshness_is_fresh_within_warming_threshold() {
        let now = Utc::now();
        let computed_at = now - Duration::seconds(PROJECTION_WARMING_AFTER_SECONDS);

        let freshness = ProjectionFreshness::from_computed_at(computed_at, now);
        assert_eq!(freshness.status, ProjectionFreshnessStatus::Fresh);
        assert_eq!(freshness.age_seconds, PROJECTION_WARMING_AFTER_SECONDS);
    }

    #[test]
    fn freshness_is_warming_before_stale_threshold() {
        let now = Utc::now();
        let computed_at = now - Duration::seconds(PROJECTION_WARMING_AFTER_SECONDS + 1);

        let freshness = ProjectionFreshness::from_computed_at(computed_at, now);
        assert_eq!(freshness.status, ProjectionFreshnessStatus::Warming);
    }

    #[test]
    fn freshness_is_stale_after_threshold() {
        let now = Utc::now();
        let computed_at = now - Duration::seconds(PROJECTION_STALE_AFTER_SECONDS + 1);

        let freshness = ProjectionFreshness::from_computed_at(computed_at, now);
        assert_eq!(freshness.status, ProjectionFreshnessStatus::Stale);
        assert_eq!(
            freshness.stale_after_seconds,
            PROJECTION_STALE_AFTER_SECONDS
        );
    }

    #[test]
    fn freshness_caps_negative_age_at_zero() {
        let now = Utc::now();
        let computed_at = now + Duration::seconds(30);

        let freshness = ProjectionFreshness::from_computed_at(computed_at, now);
        assert_eq!(freshness.age_seconds, 0);
        assert_eq!(freshness.status, ProjectionFreshnessStatus::Fresh);
    }
}
