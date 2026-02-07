use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;
use uuid::Uuid;

/// A single event in the system. Events are immutable — once written, never changed.
/// Corrections are done via compensating events (event.corrected, event.voided).
///
/// event_type is a free-form string, NOT an enum. New event types emerge from usage.
/// The system learns structure from data, not from hardcoded schemas.
#[derive(Debug, Serialize, Deserialize, ToSchema)]
pub struct Event {
    /// Unique event ID (UUIDv7 — time-sortable)
    pub id: Uuid,
    /// Owner of this event
    pub user_id: Uuid,
    /// When the event happened (as reported by the agent/user, not server time)
    pub timestamp: DateTime<Utc>,
    /// Free-form event type (e.g. "set.logged", "meal.logged", "metric.logged")
    /// NOT an enum — new types emerge from usage
    pub event_type: String,
    /// Event payload — structure depends on event_type, validated by schema registry
    pub data: serde_json::Value,
    /// Metadata about the event source
    pub metadata: EventMetadata,
}

/// Metadata about how an event was created. Not the event itself, but context about it.
#[derive(Debug, Serialize, Deserialize, ToSchema)]
pub struct EventMetadata {
    /// How the event was created: "cli", "api", "mcp", "import"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    /// Which agent created this: "claude", "gpt", "custom", etc.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent: Option<String>,
    /// Device or environment identifier
    #[serde(skip_serializing_if = "Option::is_none")]
    pub device: Option<String>,
    /// Session grouping (e.g. training session, batch import)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    /// Client-generated idempotency key for deduplication
    pub idempotency_key: String,
}

/// Request to create a new event
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateEventRequest {
    /// When the event happened
    pub timestamp: DateTime<Utc>,
    /// Free-form event type
    pub event_type: String,
    /// Event payload
    pub data: serde_json::Value,
    /// Event metadata
    pub metadata: EventMetadata,
}

/// Request to create multiple events atomically (e.g. a whole training session)
#[derive(Debug, Deserialize, ToSchema)]
pub struct BatchCreateEventsRequest {
    pub events: Vec<CreateEventRequest>,
}

/// Cursor-based pagination
#[derive(Debug, Serialize, ToSchema)]
pub struct PaginatedResponse<T: Serialize> {
    pub data: Vec<T>,
    /// Cursor for the next page. None if this is the last page.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_cursor: Option<String>,
    /// Whether there are more results after this page
    pub has_more: bool,
}
