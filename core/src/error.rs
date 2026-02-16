use serde::Serialize;
use utoipa::ToSchema;

/// Structured error response — designed for agents, not humans.
/// Every error contains enough information for an agent to understand
/// what went wrong and how to fix it.
#[derive(Debug, Serialize, ToSchema)]
pub struct ApiError {
    /// Machine-readable error code (e.g. "validation_failed", "not_found", "conflict")
    pub error: String,
    /// Domain-specific machine code for deterministic remediation (optional)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_code: Option<String>,
    /// Human/agent-readable description of what went wrong
    pub message: String,
    /// Which field caused the error (if applicable)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub field: Option<String>,
    /// The value that was received (if applicable)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub received: Option<serde_json::Value>,
    /// Request ID for tracing and debugging
    pub request_id: String,
    /// Hint about what the correct usage looks like
    #[serde(skip_serializing_if = "Option::is_none")]
    pub docs_hint: Option<String>,
    /// Recommended client action identifier (optional)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_action: Option<String>,
    /// Optional URL/deep-link target for remediation
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_action_url: Option<String>,
}

/// Error codes used across the API
pub mod codes {
    pub const VALIDATION_FAILED: &str = "validation_failed";
    pub const NOT_FOUND: &str = "not_found";
    pub const CONFLICT: &str = "conflict";
    pub const IDEMPOTENCY_CONFLICT: &str = "idempotency_conflict";
    pub const INTERNAL_ERROR: &str = "internal_error";
    pub const UNAUTHORIZED: &str = "unauthorized";
    pub const FORBIDDEN: &str = "forbidden";
    pub const RATE_LIMITED: &str = "rate_limited";
}
