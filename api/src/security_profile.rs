use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, utoipa::ToSchema)]
#[serde(rename_all = "lowercase")]
pub enum SecurityProfile {
    Default,
    Adaptive,
    Strict,
}

impl SecurityProfile {
    pub fn as_str(self) -> &'static str {
        match self {
            SecurityProfile::Default => "default",
            SecurityProfile::Adaptive => "adaptive",
            SecurityProfile::Strict => "strict",
        }
    }

    pub fn from_db_value(value: &str) -> Self {
        match value {
            "default" => SecurityProfile::Default,
            "strict" => SecurityProfile::Strict,
            _ => SecurityProfile::Adaptive,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, utoipa::ToSchema)]
pub struct SecurityProfileRolloutConfig {
    pub default_profile: SecurityProfile,
    pub adaptive_rollout_percent: i16,
    pub strict_rollout_percent: i16,
    pub updated_at: DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub updated_by: Option<Uuid>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ResolvedSecurityProfile {
    pub profile: SecurityProfile,
    pub source: String,
    pub rollout_bucket: i16,
}

#[derive(sqlx::FromRow)]
struct RolloutConfigRow {
    default_profile: String,
    adaptive_rollout_percent: i16,
    strict_rollout_percent: i16,
    updated_at: DateTime<Utc>,
    updated_by: Option<Uuid>,
    notes: Option<String>,
}

#[derive(sqlx::FromRow)]
struct ResolvedProfileRow {
    override_profile: Option<String>,
    default_profile: Option<String>,
    adaptive_rollout_percent: Option<i16>,
    strict_rollout_percent: Option<i16>,
}

pub async fn load_rollout_config(
    pool: &sqlx::PgPool,
) -> Result<SecurityProfileRolloutConfig, sqlx::Error> {
    let row = sqlx::query_as::<_, RolloutConfigRow>(
        r#"
        SELECT
            default_profile,
            adaptive_rollout_percent,
            strict_rollout_percent,
            updated_at,
            updated_by,
            notes
        FROM security_profile_rollout
        WHERE id = TRUE
        "#,
    )
    .fetch_optional(pool)
    .await?;

    Ok(match row {
        Some(row) => SecurityProfileRolloutConfig {
            default_profile: SecurityProfile::from_db_value(&row.default_profile),
            adaptive_rollout_percent: row.adaptive_rollout_percent.clamp(0, 100),
            strict_rollout_percent: row.strict_rollout_percent.clamp(0, 100),
            updated_at: row.updated_at,
            updated_by: row.updated_by,
            notes: row.notes,
        },
        None => SecurityProfileRolloutConfig {
            default_profile: SecurityProfile::Default,
            adaptive_rollout_percent: 0,
            strict_rollout_percent: 0,
            updated_at: Utc::now(),
            updated_by: None,
            notes: None,
        },
    })
}

pub async fn resolve_security_profile(
    pool: &sqlx::PgPool,
    user_id: Uuid,
) -> Result<ResolvedSecurityProfile, sqlx::Error> {
    let row = sqlx::query_as::<_, ResolvedProfileRow>(
        r#"
        SELECT
            o.profile AS override_profile,
            r.default_profile,
            r.adaptive_rollout_percent,
            r.strict_rollout_percent
        FROM (SELECT 1) seed
        LEFT JOIN security_profile_rollout r ON r.id = TRUE
        LEFT JOIN security_profile_user_overrides o ON o.user_id = $1
        "#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;

    let bucket = rollout_bucket(user_id);
    if let Some(override_profile) = row
        .as_ref()
        .and_then(|resolved_row| resolved_row.override_profile.as_deref())
    {
        return Ok(ResolvedSecurityProfile {
            profile: SecurityProfile::from_db_value(override_profile),
            source: "override".to_string(),
            rollout_bucket: bucket,
        });
    }

    let default_profile = row
        .as_ref()
        .and_then(|resolved_row| resolved_row.default_profile.as_deref())
        .map(SecurityProfile::from_db_value)
        .unwrap_or(SecurityProfile::Default);
    let strict_rollout_percent = row
        .as_ref()
        .and_then(|resolved_row| resolved_row.strict_rollout_percent)
        .unwrap_or(0)
        .clamp(0, 100);
    let adaptive_rollout_percent = row
        .as_ref()
        .and_then(|resolved_row| resolved_row.adaptive_rollout_percent)
        .unwrap_or(0)
        .clamp(0, 100);

    let strict_cutoff = strict_rollout_percent;
    let adaptive_cutoff = (strict_cutoff + adaptive_rollout_percent).min(100);
    let profile = if bucket < strict_cutoff {
        SecurityProfile::Strict
    } else if bucket < adaptive_cutoff {
        SecurityProfile::Adaptive
    } else {
        default_profile
    };

    Ok(ResolvedSecurityProfile {
        profile,
        source: "rollout".to_string(),
        rollout_bucket: bucket,
    })
}

fn rollout_bucket(user_id: Uuid) -> i16 {
    let mut hash: u64 = 1469598103934665603;
    for byte in user_id.as_bytes() {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(1099511628211);
    }
    (hash % 100) as i16
}

#[cfg(test)]
mod tests {
    use super::rollout_bucket;
    use uuid::Uuid;

    #[test]
    fn rollout_bucket_is_stable_and_bounded() {
        let user_id = Uuid::parse_str("4db3b38a-9d97-4a39-a83e-6ad5a7f1f0d4").unwrap();
        let first = rollout_bucket(user_id);
        let second = rollout_bucket(user_id);
        assert_eq!(first, second);
        assert!((0..100).contains(&first));
    }
}
