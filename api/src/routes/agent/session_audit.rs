use super::*;

pub(super) const SESSION_AUDIT_MENTION_BOUND_FIELDS: [&str; 4] =
    ["rest_seconds", "tempo", "rir", "set_type"];
pub(super) const SESSION_AUDIT_INVARIANT_ID: &str = "INV-008";
pub(super) const EVIDENCE_PARSER_VERSION: &str = "mention_parser.v1";
pub(super) const EVIDENCE_CLAIM_EVENT_TYPE: &str = "evidence.claim.logged";
pub(super) const AUDIT_CLASS_MISSING_MENTION_FIELD: &str = "missing_mention_bound_field";
pub(super) const AUDIT_CLASS_SESSION_BLOCK_REQUIRED_FIELD: &str =
    "session_block_required_field_missing";
pub(super) const AUDIT_CLASS_SCALE_OUT_OF_BOUNDS: &str = "scale_out_of_bounds";
pub(super) const AUDIT_CLASS_NARRATIVE_CONTRADICTION: &str = "narrative_structured_contradiction";
pub(super) const AUDIT_CLASS_UNSUPPORTED_INFERRED: &str = "unsupported_inferred_value";
pub(super) const SESSION_FEEDBACK_CERTAINTY_CONFIRMED: &str = "confirmed";
pub(super) const SESSION_FEEDBACK_CERTAINTY_INFERRED: &str = "inferred";
pub(super) const SESSION_FEEDBACK_CERTAINTY_UNRESOLVED: &str = "unresolved";
pub(super) const SESSION_FEEDBACK_CONTEXT_KEYS: [&str; 6] = [
    "context",
    "context_text",
    "summary",
    "comment",
    "notes",
    "feeling",
];
pub(super) const SESSION_POSITIVE_HINTS: [&str; 9] = [
    "good", "great", "fun", "strong", "solid", "leicht", "easy", "well", "locker",
];
pub(super) const SESSION_NEGATIVE_HINTS: [&str; 10] = [
    "bad", "terrible", "schlecht", "pain", "hurt", "injury", "müde", "tired", "awful", "weak",
];
pub(super) const SESSION_EASY_HINTS: [&str; 5] = ["easy", "leicht", "locker", "chill", "smooth"];
pub(super) const SESSION_HARD_HINTS: [&str; 8] = [
    "hard",
    "brutal",
    "tough",
    "exhausting",
    "all-out",
    "maxed",
    "heavy",
    "grindy",
];

static TEMPO_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\btempo\s*[:=]?\s*(\d-[\dx]-[\dx]-[\dx])\b").expect("valid tempo regex")
});
static TEMPO_BARE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(\d-[\dx]-[\dx]-[\dx])\b").expect("valid tempo bare"));
static RIR_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?:rir\s*[:=]?\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*rir|(\d+)\s*reps?\s+in\s+reserve)\b",
    )
    .expect("valid rir regex")
});
static REST_MMSS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:rest|pause|break|satzpause)\s*[:=]?\s*(\d{1,2}):(\d{2})\b")
        .expect("valid rest mmss regex")
});
static REST_SECONDS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?:(?:rest|pause|break|satzpause)\s*[:=]?\s*(\d{1,3})\s*(?:s|sec|secs|second|seconds)|(\d{1,3})\s*(?:s|sec|secs|second|seconds)\s*(?:rest|pause|break|satzpause))\b",
    )
    .expect("valid rest seconds regex")
});
static REST_MINUTES_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?:(?:rest|pause|break|satzpause)\s*[:=]?\s*(\d{1,2})\s*(?:m|min|mins|minute|minutes)|(\d{1,2})\s*(?:m|min|mins|minute|minutes)\s*(?:rest|pause|break|satzpause))\b",
    )
    .expect("valid rest minutes regex")
});
static REST_NUMBER_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:rest|pause|break|satzpause)\s*[:=]?\s*(\d{1,3})\b")
        .expect("valid rest number regex")
});
static SET_TYPE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(warm[\s-]?up|back[\s-]?off|amrap|working)\b").expect("valid set type regex")
});

#[derive(Debug, Clone)]
pub(super) struct MentionValueWithSpan {
    pub(super) value: Value,
    pub(super) unit: Option<String>,
    pub(super) span_start: usize,
    pub(super) span_end: usize,
    pub(super) span_text: String,
    pub(super) confidence: f64,
}

#[derive(Debug, Clone)]
pub(super) struct EvidenceClaimDraft {
    pub(super) claim_type: String,
    pub(super) value: Value,
    pub(super) unit: Option<String>,
    pub(super) confidence: f64,
    pub(super) source_field: String,
    pub(super) source_text: String,
    pub(super) span_start: usize,
    pub(super) span_end: usize,
    pub(super) span_text: String,
}

pub(super) fn round_to_two(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

pub(super) fn normalize_rest_seconds(value: f64) -> Option<f64> {
    if !value.is_finite() || value < 0.0 {
        return None;
    }
    Some(round_to_two(value))
}

pub(super) fn normalize_rir(value: f64) -> Option<f64> {
    if !value.is_finite() {
        return None;
    }
    Some(round_to_two(value.clamp(0.0, 10.0)))
}

pub(super) fn parse_rest_with_span(text: &str) -> Option<MentionValueWithSpan> {
    if let Some(caps) = REST_MMSS_RE.captures(text) {
        let minutes = caps.get(1)?.as_str().parse::<f64>().ok()?;
        let seconds = caps.get(2)?.as_str().parse::<f64>().ok()?;
        let value = normalize_rest_seconds((minutes * 60.0) + seconds)?;
        let full = caps.get(0)?;
        return Some(MentionValueWithSpan {
            value: mention_value_from_number(value)?,
            unit: Some("seconds".to_string()),
            span_start: full.start(),
            span_end: full.end(),
            span_text: full.as_str().to_string(),
            confidence: 0.95,
        });
    }
    if let Some(caps) = REST_SECONDS_RE.captures(text) {
        let raw = caps
            .get(1)
            .or_else(|| caps.get(2))
            .map(|m| m.as_str())
            .and_then(|raw| raw.parse::<f64>().ok())?;
        let value = normalize_rest_seconds(raw)?;
        let full = caps.get(0)?;
        return Some(MentionValueWithSpan {
            value: mention_value_from_number(value)?,
            unit: Some("seconds".to_string()),
            span_start: full.start(),
            span_end: full.end(),
            span_text: full.as_str().to_string(),
            confidence: 0.95,
        });
    }
    if let Some(caps) = REST_MINUTES_RE.captures(text) {
        let raw = caps
            .get(1)
            .or_else(|| caps.get(2))
            .map(|m| m.as_str())
            .and_then(|raw| raw.parse::<f64>().ok())?;
        let value = normalize_rest_seconds(raw * 60.0)?;
        let full = caps.get(0)?;
        return Some(MentionValueWithSpan {
            value: mention_value_from_number(value)?,
            unit: Some("seconds".to_string()),
            span_start: full.start(),
            span_end: full.end(),
            span_text: full.as_str().to_string(),
            confidence: 0.93,
        });
    }
    if let Some(caps) = REST_NUMBER_RE.captures(text) {
        let raw = caps.get(1)?.as_str().parse::<f64>().ok()?;
        let value = normalize_rest_seconds(raw)?;
        let full = caps.get(0)?;
        return Some(MentionValueWithSpan {
            value: mention_value_from_number(value)?,
            unit: Some("seconds".to_string()),
            span_start: full.start(),
            span_end: full.end(),
            span_text: full.as_str().to_string(),
            confidence: 0.9,
        });
    }
    None
}

pub(super) fn parse_rest_seconds_from_text(text: &str) -> Option<f64> {
    parse_rest_with_span(text).and_then(|parsed| parsed.value.as_f64())
}

pub(super) fn parse_rir_with_span(text: &str) -> Option<MentionValueWithSpan> {
    let caps = RIR_RE.captures(text)?;
    let raw = caps
        .get(1)
        .or_else(|| caps.get(2))
        .or_else(|| caps.get(3))
        .map(|m| m.as_str())?;
    let value = normalize_rir(raw.parse::<f64>().ok()?)?;
    let full = caps.get(0)?;
    Some(MentionValueWithSpan {
        value: mention_value_from_number(value)?,
        unit: Some("reps_in_reserve".to_string()),
        span_start: full.start(),
        span_end: full.end(),
        span_text: full.as_str().to_string(),
        confidence: 0.95,
    })
}

pub(super) fn parse_rir_from_text(text: &str) -> Option<f64> {
    parse_rir_with_span(text).and_then(|parsed| parsed.value.as_f64())
}

pub(super) fn parse_tempo_with_span(text: &str) -> Option<MentionValueWithSpan> {
    let caps = TEMPO_RE
        .captures(text)
        .or_else(|| TEMPO_BARE_RE.captures(text))?;
    let raw = caps.get(1)?.as_str().trim().to_lowercase();
    if raw.is_empty() {
        return None;
    }
    let full = caps.get(0)?;
    Some(MentionValueWithSpan {
        value: Value::String(raw),
        unit: None,
        span_start: full.start(),
        span_end: full.end(),
        span_text: full.as_str().to_string(),
        confidence: 0.95,
    })
}

pub(super) fn parse_tempo_from_text(text: &str) -> Option<String> {
    parse_tempo_with_span(text).and_then(|parsed| parsed.value.as_str().map(str::to_string))
}

pub(super) fn normalize_set_type(value: &str) -> Option<String> {
    let text = value.trim().to_lowercase();
    if text.is_empty() {
        return None;
    }
    for (needle, canonical) in [
        ("warmup", "warmup"),
        ("warm-up", "warmup"),
        ("backoff", "backoff"),
        ("back-off", "backoff"),
        ("amrap", "amrap"),
        ("working", "working"),
    ] {
        if text.contains(needle) {
            return Some(canonical.to_string());
        }
    }
    None
}

pub(super) fn parse_set_type_with_span(text: &str) -> Option<MentionValueWithSpan> {
    let captures = SET_TYPE_RE.captures(text)?;
    let matched = captures.get(1)?;
    let canonical = normalize_set_type(matched.as_str())?;
    Some(MentionValueWithSpan {
        value: Value::String(canonical),
        unit: None,
        span_start: matched.start(),
        span_end: matched.end(),
        span_text: matched.as_str().to_string(),
        confidence: 0.9,
    })
}

pub(super) fn mention_value_from_number(value: f64) -> Option<Value> {
    serde_json::Number::from_f64(value).map(Value::Number)
}

pub(super) fn extract_set_context_mentions_from_text(text: &str) -> HashMap<&'static str, Value> {
    let mut mentions = HashMap::new();
    let normalized = text.trim().to_lowercase();
    if normalized.is_empty() {
        return mentions;
    }

    if let Some(value) =
        parse_rest_seconds_from_text(&normalized).and_then(mention_value_from_number)
    {
        mentions.insert("rest_seconds", value);
    }
    if let Some(value) = parse_rir_from_text(&normalized).and_then(mention_value_from_number) {
        mentions.insert("rir", value);
    }
    if let Some(value) = parse_tempo_from_text(&normalized) {
        mentions.insert("tempo", Value::String(value));
    }
    if let Some(value) = normalize_set_type(&normalized) {
        mentions.insert("set_type", Value::String(value));
    }

    mentions
}

pub(super) fn event_text_candidates(event: &CreateEventRequest) -> Vec<&str> {
    event_text_candidates_with_source(event)
        .into_iter()
        .map(|(_, text)| text)
        .collect()
}

pub(super) fn event_text_candidates_with_source(
    event: &CreateEventRequest,
) -> Vec<(&'static str, &str)> {
    let mut out = Vec::new();
    for key in ["notes", "context_text", "utterance"] {
        if let Some(text) = event.data.get(key).and_then(Value::as_str) {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                out.push((key, trimmed));
            }
        }
    }
    out
}

pub(super) fn extract_evidence_claim_drafts(event: &CreateEventRequest) -> Vec<EvidenceClaimDraft> {
    let mut drafts = Vec::new();
    for (source_field, source_text) in event_text_candidates_with_source(event) {
        if let Some(parsed) = parse_rest_with_span(source_text) {
            drafts.push(EvidenceClaimDraft {
                claim_type: "set_context.rest_seconds".to_string(),
                value: parsed.value,
                unit: parsed.unit,
                confidence: parsed.confidence,
                source_field: source_field.to_string(),
                source_text: source_text.to_string(),
                span_start: parsed.span_start,
                span_end: parsed.span_end,
                span_text: parsed.span_text,
            });
        }
        if let Some(parsed) = parse_rir_with_span(source_text) {
            drafts.push(EvidenceClaimDraft {
                claim_type: "set_context.rir".to_string(),
                value: parsed.value,
                unit: parsed.unit,
                confidence: parsed.confidence,
                source_field: source_field.to_string(),
                source_text: source_text.to_string(),
                span_start: parsed.span_start,
                span_end: parsed.span_end,
                span_text: parsed.span_text,
            });
        }
        if let Some(parsed) = parse_tempo_with_span(source_text) {
            drafts.push(EvidenceClaimDraft {
                claim_type: "set_context.tempo".to_string(),
                value: parsed.value,
                unit: parsed.unit,
                confidence: parsed.confidence,
                source_field: source_field.to_string(),
                source_text: source_text.to_string(),
                span_start: parsed.span_start,
                span_end: parsed.span_end,
                span_text: parsed.span_text,
            });
        }
        if let Some(parsed) = parse_set_type_with_span(source_text) {
            drafts.push(EvidenceClaimDraft {
                claim_type: "set_context.set_type".to_string(),
                value: parsed.value,
                unit: parsed.unit,
                confidence: parsed.confidence,
                source_field: source_field.to_string(),
                source_text: source_text.to_string(),
                span_start: parsed.span_start,
                span_end: parsed.span_end,
                span_text: parsed.span_text,
            });
        }
    }
    drafts
}

pub(super) fn evidence_scope_for_event(event: &CreateEventRequest) -> Value {
    let scope_level = if event.event_type.trim().eq_ignore_ascii_case("set.logged") {
        "set"
    } else {
        "session"
    };
    let session_id = event.metadata.session_id.clone();
    let exercise_id = event
        .data
        .get("exercise_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    serde_json::json!({
        "level": scope_level,
        "event_type": event.event_type,
        "session_id": session_id,
        "exercise_id": exercise_id,
    })
}

pub(super) fn build_evidence_claim_events(
    user_id: Uuid,
    events: &[CreateEventRequest],
    receipts: &[AgentWriteReceipt],
) -> Vec<CreateEventRequest> {
    let mut claim_events = Vec::new();
    let mut seen_idempotency_keys: HashSet<String> = HashSet::new();

    for (index, event) in events.iter().enumerate() {
        let Some(receipt) = receipts.get(index) else {
            continue;
        };
        for draft in extract_evidence_claim_drafts(event) {
            let value_fingerprint = canonical_mention_value(&draft.value);
            let seed = format!(
                "{}|{}|{}|{}|{}|{}|{}|{}",
                user_id,
                receipt.event_id,
                draft.claim_type,
                value_fingerprint,
                draft.source_field,
                draft.span_start,
                draft.span_end,
                EVIDENCE_PARSER_VERSION
            );
            let claim_id = format!("claim_{}", stable_hash_suffix(&seed, 24));
            let idempotency_key = format!("evidence-claim-{claim_id}");
            if !seen_idempotency_keys.insert(idempotency_key.clone()) {
                continue;
            }

            claim_events.push(CreateEventRequest {
                timestamp: event.timestamp,
                event_type: EVIDENCE_CLAIM_EVENT_TYPE.to_string(),
                data: serde_json::json!({
                    "claim_id": claim_id,
                    "claim_type": draft.claim_type,
                    "value": draft.value,
                    "unit": draft.unit,
                    "scope": evidence_scope_for_event(event),
                    "confidence": draft.confidence,
                    "provenance": {
                        "source_field": draft.source_field,
                        "source_text": draft.source_text,
                        "source_text_span": {
                            "start": draft.span_start,
                            "end": draft.span_end,
                            "text": draft.span_text,
                        },
                        "parser_version": EVIDENCE_PARSER_VERSION,
                    },
                    "lineage": {
                        "event_id": receipt.event_id,
                        "event_type": receipt.event_type,
                        "lineage_type": "supports",
                    },
                }),
                metadata: EventMetadata {
                    source: Some("agent_write_with_proof".to_string()),
                    agent: Some("api".to_string()),
                    device: None,
                    session_id: event.metadata.session_id.clone(),
                    idempotency_key,
                },
            });
        }
    }

    claim_events
}

pub(super) fn event_structured_field_present(event: &CreateEventRequest, field: &str) -> bool {
    event
        .data
        .get(field)
        .map(|value| !value.is_null())
        .unwrap_or(false)
}

pub(super) fn canonical_mention_value(value: &Value) -> String {
    if let Some(number) = value.as_f64() {
        return format!("{:.2}", number);
    }
    value
        .as_str()
        .map(|s| s.trim().to_lowercase())
        .unwrap_or_else(|| value.to_string())
}

pub(super) fn extract_session_feedback_context(event: &CreateEventRequest) -> Option<String> {
    for key in SESSION_FEEDBACK_CONTEXT_KEYS {
        if let Some(text) = event.data.get(key).and_then(Value::as_str) {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_lowercase());
            }
        }
    }
    None
}

pub(super) fn extract_feedback_scale_value(event: &CreateEventRequest, field: &str) -> Option<f64> {
    event.data.get(field).and_then(Value::as_f64)
}

pub(super) fn contains_any_hint(text: &str, hints: &[&str]) -> bool {
    hints.iter().any(|hint| text.contains(hint))
}

pub(super) fn has_unsupported_inferred_value(event: &CreateEventRequest, field: &str) -> bool {
    let source_key = format!("{field}_source");
    let evidence_key = format!("{field}_evidence_claim_id");
    let is_inferred = event
        .data
        .get(source_key.as_str())
        .and_then(Value::as_str)
        .map(|value| value.eq_ignore_ascii_case("inferred"))
        .unwrap_or(false);
    if !is_inferred {
        return false;
    }
    event
        .data
        .get(evidence_key.as_str())
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .is_none()
}

pub(super) fn parse_non_empty_lower_str(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| value.to_ascii_lowercase())
}

pub(super) fn has_non_null_field(event: &CreateEventRequest, field: &str) -> bool {
    event
        .data
        .get(field)
        .map(|value| !value.is_null())
        .unwrap_or(false)
}

fn session_block_scope_label(block_index: usize, block_type: Option<&str>) -> String {
    let index = block_index + 1;
    let normalized = block_type
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| value.to_ascii_lowercase());
    match normalized {
        Some(block_type) => format!("Block {index} ({block_type})"),
        None => format!("Block {index}"),
    }
}

fn has_non_null_work_dimension(work_obj: &serde_json::Map<String, Value>) -> bool {
    for key in ["duration_seconds", "distance_meters", "reps", "contacts"] {
        if work_obj.get(key).is_some_and(|value| !value.is_null()) {
            return true;
        }
    }
    false
}

fn block_explicitly_marks_anchors_not_applicable(
    block_obj: &serde_json::Map<String, Value>,
) -> bool {
    block_obj
        .get("intensity_anchors_status")
        .and_then(Value::as_str)
        .map(str::trim)
        .map(|value| value.eq_ignore_ascii_case("not_applicable"))
        .unwrap_or(false)
}

fn block_has_anchor_entries(block_obj: &serde_json::Map<String, Value>) -> bool {
    block_obj
        .get("intensity_anchors")
        .and_then(Value::as_array)
        .is_some_and(|anchors| !anchors.is_empty())
}

fn performance_block_requires_anchor(block_type: &str) -> bool {
    !block_type.eq_ignore_ascii_case("recovery_session")
}

pub(super) fn collect_session_logged_required_field_gaps(
    event: &CreateEventRequest,
) -> Vec<SessionAuditUnresolved> {
    let mut unresolved: Vec<SessionAuditUnresolved> = Vec::new();
    let Some(blocks) = event.data.get("blocks").and_then(Value::as_array) else {
        unresolved.push(SessionAuditUnresolved {
            exercise_label: "Session".to_string(),
            field: "blocks".to_string(),
            candidates: Vec::new(),
        });
        return unresolved;
    };

    if blocks.is_empty() {
        unresolved.push(SessionAuditUnresolved {
            exercise_label: "Session".to_string(),
            field: "blocks".to_string(),
            candidates: Vec::new(),
        });
        return unresolved;
    }

    for (block_index, block) in blocks.iter().enumerate() {
        let Some(block_obj) = block.as_object() else {
            unresolved.push(SessionAuditUnresolved {
                exercise_label: session_block_scope_label(block_index, None),
                field: "block".to_string(),
                candidates: Vec::new(),
            });
            continue;
        };
        let block_type = block_obj
            .get("block_type")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty());
        let scope_label = session_block_scope_label(block_index, block_type);

        if block_type.is_none() {
            unresolved.push(SessionAuditUnresolved {
                exercise_label: scope_label.clone(),
                field: "block_type".to_string(),
                candidates: Vec::new(),
            });
            continue;
        }

        let Some(dose_obj) = block_obj.get("dose").and_then(Value::as_object) else {
            unresolved.push(SessionAuditUnresolved {
                exercise_label: scope_label.clone(),
                field: "dose".to_string(),
                candidates: Vec::new(),
            });
            continue;
        };

        let has_work_dimension = dose_obj
            .get("work")
            .and_then(Value::as_object)
            .map(has_non_null_work_dimension)
            .unwrap_or(false);
        if !has_work_dimension {
            unresolved.push(SessionAuditUnresolved {
                exercise_label: scope_label.clone(),
                field: "dose.work".to_string(),
                candidates: Vec::new(),
            });
        }

        let block_type_value = block_type.unwrap_or_default();
        if performance_block_requires_anchor(block_type_value)
            && !block_has_anchor_entries(block_obj)
            && !block_explicitly_marks_anchors_not_applicable(block_obj)
        {
            unresolved.push(SessionAuditUnresolved {
                exercise_label: scope_label,
                field: "blocks.intensity_anchors".to_string(),
                candidates: Vec::new(),
            });
        }
    }

    unresolved
}

pub(super) fn validate_session_feedback_certainty_contract(
    events: &[CreateEventRequest],
) -> Result<(), AppError> {
    for (index, event) in events.iter().enumerate() {
        if !event.event_type.eq_ignore_ascii_case("session.completed") {
            continue;
        }

        for field in ["enjoyment", "perceived_quality", "perceived_exertion"] {
            let state_key = format!("{field}_state");
            let source_key = format!("{field}_source");
            let evidence_key = format!("{field}_evidence_claim_id");
            let unresolved_reason_key = format!("{field}_unresolved_reason");
            let field_path = format!("events[{index}].data.{field}");
            let state = parse_non_empty_lower_str(event.data.get(state_key.as_str()));
            let source = parse_non_empty_lower_str(event.data.get(source_key.as_str()));
            let has_value = has_non_null_field(event, field);
            let has_evidence =
                parse_non_empty_lower_str(event.data.get(evidence_key.as_str())).is_some();
            let has_unresolved_reason =
                parse_non_empty_lower_str(event.data.get(unresolved_reason_key.as_str())).is_some();

            if let Some(state_value) = state.as_deref() {
                if ![
                    SESSION_FEEDBACK_CERTAINTY_CONFIRMED,
                    SESSION_FEEDBACK_CERTAINTY_INFERRED,
                    SESSION_FEEDBACK_CERTAINTY_UNRESOLVED,
                ]
                .contains(&state_value)
                {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_certainty_invalid_state".to_string(),
                        message: format!(
                            "{field} has invalid certainty state '{state_value}'. Allowed: confirmed|inferred|unresolved"
                        ),
                        field: Some(format!("{field_path}_state")),
                        received: event.data.get(state_key.as_str()).cloned(),
                        docs_hint: Some(
                            "Set <field>_state to confirmed, inferred, or unresolved. ".to_string(),
                        ),
                    });
                }
            }

            if let Some(source_value) = source.as_deref() {
                if !["explicit", "user_confirmed", "estimated", "inferred"].contains(&source_value)
                {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_source_invalid".to_string(),
                        message: format!(
                            "{field} has invalid source '{source_value}'. Allowed: explicit|user_confirmed|estimated|inferred"
                        ),
                        field: Some(format!("{field_path}_source")),
                        received: event.data.get(source_key.as_str()).cloned(),
                        docs_hint: Some(
                            "Use canonical source labels for session feedback provenance."
                                .to_string(),
                        ),
                    });
                }
            }

            if matches!(state.as_deref(), Some(SESSION_FEEDBACK_CERTAINTY_CONFIRMED)) && !has_value
            {
                return Err(AppError::PolicyViolation {
                    code: "session_feedback_confirmed_missing_value".to_string(),
                    message: format!("{field} is marked confirmed but no value was provided."),
                    field: Some(field_path.clone()),
                    received: event.data.get(field).cloned(),
                    docs_hint: Some(
                        "When <field>_state=confirmed, provide the numeric <field> value."
                            .to_string(),
                    ),
                });
            }

            if matches!(state.as_deref(), Some(SESSION_FEEDBACK_CERTAINTY_INFERRED))
                || matches!(source.as_deref(), Some("inferred"))
            {
                if !has_value {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_inferred_missing_value".to_string(),
                        message: format!(
                            "{field} is marked inferred but no value was provided."
                        ),
                        field: Some(field_path.clone()),
                        received: event.data.get(field).cloned(),
                        docs_hint: Some(
                            "When certainty/source is inferred, include the inferred numeric value."
                                .to_string(),
                        ),
                    });
                }
                if !has_evidence {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_inferred_missing_evidence".to_string(),
                        message: format!(
                            "{field} is inferred but missing {field}_evidence_claim_id."
                        ),
                        field: Some(format!("{field_path}_evidence_claim_id")),
                        received: event.data.get(evidence_key.as_str()).cloned(),
                        docs_hint: Some(
                            "Inferred subjective values require a linked evidence claim id."
                                .to_string(),
                        ),
                    });
                }
            }

            if matches!(
                state.as_deref(),
                Some(SESSION_FEEDBACK_CERTAINTY_UNRESOLVED)
            ) {
                if has_value {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_unresolved_has_value".to_string(),
                        message: format!(
                            "{field} is marked unresolved but a numeric value was still provided."
                        ),
                        field: Some(field_path.clone()),
                        received: event.data.get(field).cloned(),
                        docs_hint: Some(
                            "Use unresolved state only when no value is persisted yet.".to_string(),
                        ),
                    });
                }
                if !has_unresolved_reason {
                    return Err(AppError::PolicyViolation {
                        code: "session_feedback_unresolved_missing_reason".to_string(),
                        message: format!(
                            "{field} is unresolved but {field}_unresolved_reason is missing."
                        ),
                        field: Some(format!("{field_path}_unresolved_reason")),
                        received: event.data.get(unresolved_reason_key.as_str()).cloned(),
                        docs_hint: Some(
                            "Provide a short unresolved reason so the agent can ask one precise follow-up question."
                                .to_string(),
                        ),
                    });
                }
            }
        }
    }

    Ok(())
}

pub(super) fn audit_field_label(field: &str) -> &'static str {
    match field {
        "blocks" => "Session-Blöcke",
        "block" => "Block-Objekt",
        "block_type" => "Blocktyp",
        "dose" => "Block-Dosis",
        "dose.work" => "Work-Dosis",
        "blocks.intensity_anchors" => "Intensitätsanker",
        "rest_seconds" => "Satzpause",
        "tempo" => "Tempo",
        "rir" => "RIR",
        "set_type" => "Satztyp",
        "enjoyment" => "Session-Freude",
        "perceived_quality" => "Session-Qualität",
        "perceived_exertion" => "Session-Anstrengung",
        _ => "Feld",
    }
}

pub(super) fn format_value_for_question(value: &str) -> String {
    if let Ok(parsed) = value.parse::<f64>() {
        if (parsed.fract()).abs() < f64::EPSILON {
            return format!("{}", parsed as i64);
        }
        return format!("{parsed:.2}");
    }
    value.to_string()
}

pub(super) fn exercise_label_for_event(event: &CreateEventRequest) -> String {
    for key in ["exercise_id", "exercise"] {
        if let Some(label) = event.data.get(key).and_then(Value::as_str) {
            let trimmed = label.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    "diesem Satz".to_string()
}

pub(super) fn build_clarification_question(
    unresolved: &[SessionAuditUnresolved],
) -> Option<String> {
    let first = unresolved.first()?;
    if first.candidates.is_empty() {
        if first.field == "blocks.intensity_anchors" {
            return Some(format!(
                "Bitte ergänzen: {} bei {}. Nutze mindestens einen Anker (z. B. Pace, Power, Borg/RPE, % Referenz oder Herzfrequenz) oder setze intensity_anchors_status=not_applicable.",
                audit_field_label(&first.field),
                first.exercise_label,
            ));
        }
        return Some(format!(
            "Bitte ergänzen: {} bei {}.",
            audit_field_label(&first.field),
            first.exercise_label
        ));
    }
    if first.candidates.len() > 1 {
        let values = first
            .candidates
            .iter()
            .map(|v| format_value_for_question(v))
            .collect::<Vec<_>>()
            .join(" oder ");
        return Some(format!(
            "Konflikt bei {}: {} = {}. Welcher Wert stimmt?",
            first.exercise_label,
            audit_field_label(&first.field),
            values
        ));
    }
    let value = first
        .candidates
        .first()
        .map(|v| format_value_for_question(v))
        .unwrap_or_else(|| "einen Wert".to_string());
    Some(format!(
        "Bitte bestätigen: {} bei {} = {}?",
        audit_field_label(&first.field),
        first.exercise_label,
        value
    ))
}

pub(super) fn summarize_inferred_provenance(provenance: &Value) -> String {
    if let Some(text) = provenance
        .get("source_text_span")
        .and_then(Value::as_object)
        .and_then(|span| span.get("text"))
        .and_then(Value::as_str)
    {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }
    if let Some(text) = provenance.get("source_text").and_then(Value::as_str) {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }
    provenance
        .get("source_type")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| "provenance_not_available".to_string())
}

pub(super) fn collect_reliability_inferred_facts(
    evidence_events: &[CreateEventRequest],
    repair_events: &[CreateEventRequest],
) -> Vec<AgentReliabilityInferredFact> {
    let mut facts = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();

    for event in evidence_events {
        if !event
            .event_type
            .eq_ignore_ascii_case(EVIDENCE_CLAIM_EVENT_TYPE)
        {
            continue;
        }
        let field = event
            .data
            .get("claim_type")
            .and_then(Value::as_str)
            .unwrap_or("unknown_claim")
            .trim()
            .to_string();
        if field.is_empty() {
            continue;
        }
        let confidence = event
            .data
            .get("confidence")
            .and_then(Value::as_f64)
            .unwrap_or(0.0)
            .clamp(0.0, 1.0);
        let provenance =
            summarize_inferred_provenance(event.data.get("provenance").unwrap_or(&Value::Null));
        let dedup_key = format!("evidence|{field}|{provenance}");
        if seen.insert(dedup_key) {
            facts.push(AgentReliabilityInferredFact {
                field,
                confidence,
                provenance,
            });
        }
    }

    for event in repair_events {
        if !event.event_type.eq_ignore_ascii_case("set.corrected")
            && !event.event_type.eq_ignore_ascii_case("session.completed")
        {
            continue;
        }

        let repair_provenance = event.data.get("repair_provenance").unwrap_or(&Value::Null);
        let source_type = repair_provenance
            .get("source_type")
            .and_then(Value::as_str)
            .unwrap_or("");
        if !source_type.eq_ignore_ascii_case("inferred") {
            continue;
        }
        let confidence = repair_provenance
            .get("confidence")
            .and_then(Value::as_f64)
            .unwrap_or(0.0)
            .clamp(0.0, 1.0);
        let provenance = repair_provenance
            .get("reason")
            .and_then(Value::as_str)
            .map(str::to_string)
            .or_else(|| {
                event
                    .data
                    .get("reason")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .unwrap_or_else(|| "repair_provenance_not_available".to_string());

        if event.event_type.eq_ignore_ascii_case("set.corrected") {
            let changed_fields = event
                .data
                .get("changed_fields")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            for field in changed_fields.keys() {
                let dedup_key = format!("repair|set.corrected.{field}|{provenance}");
                if seen.insert(dedup_key) {
                    facts.push(AgentReliabilityInferredFact {
                        field: format!("set.corrected.{field}"),
                        confidence,
                        provenance: provenance.clone(),
                    });
                }
            }
            continue;
        }

        let inferred_fields = event
            .data
            .as_object()
            .map(|obj| {
                obj.iter()
                    .filter_map(|(key, value)| {
                        if !key.ends_with("_source")
                            || !value
                                .as_str()
                                .map(|source| source.eq_ignore_ascii_case("inferred"))
                                .unwrap_or(false)
                        {
                            return None;
                        }
                        Some(key.trim_end_matches("_source").to_string())
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        for field in inferred_fields {
            let dedup_key = format!("repair|session.completed.{field}|{provenance}");
            if seen.insert(dedup_key) {
                facts.push(AgentReliabilityInferredFact {
                    field: format!("session.completed.{field}"),
                    confidence,
                    provenance: provenance.clone(),
                });
            }
        }
    }

    facts
}

pub(super) fn build_reliability_ux(
    claim_guard: &AgentWriteClaimGuard,
    session_audit: &AgentSessionAuditSummary,
    inferred_facts: Vec<AgentReliabilityInferredFact>,
) -> AgentReliabilityUx {
    if !claim_guard.allow_saved_claim || session_audit.status == "needs_clarification" {
        let assistant_phrase = if let Some(question) =
            session_audit.clarification_question.as_deref()
        {
            format!("Unresolved: Es gibt einen Konflikt. {}", question.trim())
        } else if claim_guard.claim_status == "failed" {
            "Unresolved: Write-Proof ist unvollständig; bitte erneut mit denselben Idempotency-Keys versuchen.".to_string()
        } else {
            "Unresolved: Verifikation läuft noch; bitte noch keinen finalen 'saved'-Claim machen."
                .to_string()
        };
        return AgentReliabilityUx {
            state: "unresolved".to_string(),
            assistant_phrase,
            inferred_facts,
            clarification_question: session_audit.clarification_question.clone(),
        };
    }

    if !inferred_facts.is_empty() || session_audit.status == "repaired" {
        let assistant_phrase = inferred_facts
            .first()
            .map(|item| {
                format!(
                    "Inferred: Speicherung ist verifiziert, aber mindestens ein Wert ist inferiert ({} @ {:.2}, Quelle: {}).",
                    item.field,
                    item.confidence,
                    item.provenance
                )
            })
            .unwrap_or_else(|| {
                "Inferred: Speicherung ist verifiziert, enthält aber inferierte Audit-Reparaturen mit Provenance."
                    .to_string()
            });
        return AgentReliabilityUx {
            state: "inferred".to_string(),
            assistant_phrase,
            inferred_facts,
            clarification_question: None,
        };
    }

    AgentReliabilityUx {
        state: "saved".to_string(),
        assistant_phrase: "Saved: Speicherung ist verifiziert (Receipt + Read-after-Write)."
            .to_string(),
        inferred_facts,
        clarification_question: None,
    }
}
