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

/// A plausibility warning on an event. Not an error — the event is still stored.
/// Signals that a value looks unusual and the agent should verify.
#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct EventWarning {
    /// Which field triggered the warning
    pub field: String,
    /// Human/agent-readable description
    pub message: String,
    /// Always "warning" — events are never rejected
    pub severity: String,
}

/// Response for single event creation — event + optional plausibility warnings.
/// When warnings is empty, the field is omitted (backward compatible).
#[derive(Debug, Serialize, ToSchema)]
pub struct CreateEventResponse {
    /// The created event
    #[serde(flatten)]
    pub event: Event,
    /// Plausibility warnings (empty = omitted from JSON)
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<EventWarning>,
}

/// Response for batch event creation — events + optional plausibility warnings.
/// Each warning includes event_index to identify which event it belongs to.
#[derive(Debug, Serialize, ToSchema)]
pub struct BatchCreateEventsResponse {
    /// The created events
    pub events: Vec<Event>,
    /// Plausibility warnings across all events (empty = omitted)
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<BatchEventWarning>,
}

/// A warning for a specific event in a batch.
#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct BatchEventWarning {
    /// Index of the event in the batch (0-based)
    pub event_index: usize,
    /// Which field triggered the warning
    pub field: String,
    /// Human/agent-readable description
    pub message: String,
    /// Always "warning"
    pub severity: String,
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
