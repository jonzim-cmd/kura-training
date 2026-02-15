use super::*;

pub(super) const PERSIST_INTENT_SCHEMA_VERSION: &str = "persist_intent_policy.v1";
const MAX_REASON_CODES: usize = 8;

#[derive(Debug)]
pub(super) struct PersistIntentComputation {
    pub(super) persist_intent: AgentPersistIntent,
    pub(super) draft_events: Vec<CreateEventRequest>,
}

fn push_reason_code(reason_codes: &mut Vec<String>, code: &str) {
    if reason_codes.iter().any(|existing| existing == code) {
        return;
    }
    reason_codes.push(code.to_string());
}

fn persist_topic_for_event(event: &CreateEventRequest) -> Option<&'static str> {
    let event_type = event.event_type.trim().to_lowercase();
    match event_type.as_str() {
        "set.logged" | "set.corrected" => Some("training_set"),
        "session.logged" => Some("training_session"),
        "session.completed" => Some("session_feedback"),
        "preference.set" => Some("preference"),
        "profile.updated" | "goal.set" | "injury.reported" | "bodyweight.logged" => Some("profile"),
        "observation.logged" => Some("observation"),
        _ => {
            if is_planning_or_coaching_event_type(&event_type) {
                Some("plan_update")
            } else if !event_text_candidates(event).is_empty() {
                Some("other")
            } else {
                None
            }
        }
    }
}

fn grouped_items(events: &[CreateEventRequest]) -> Vec<AgentPersistIntentGroupedItem> {
    let mut grouped: BTreeMap<String, (usize, BTreeMap<String, ()>)> = BTreeMap::new();
    for event in events {
        let Some(topic) = persist_topic_for_event(event) else {
            continue;
        };
        let event_type = event.event_type.trim().to_lowercase();
        let entry = grouped
            .entry(topic.to_string())
            .or_insert_with(|| (0, BTreeMap::new()));
        entry.0 += 1;
        entry.1.insert(event_type, ());
    }

    grouped
        .into_iter()
        .map(
            |(topic, (count, event_types))| AgentPersistIntentGroupedItem {
                topic,
                count,
                event_types: event_types.into_keys().collect(),
            },
        )
        .collect()
}

fn build_single_confirmation_prompt(grouped: &[AgentPersistIntentGroupedItem]) -> Option<String> {
    let first = grouped.first()?;
    let prompt = match first.topic.as_str() {
        "training_set" | "training_session" | "session_feedback" => {
            "Soll ich diese Trainingsergebnisse jetzt final in Kura speichern?"
        }
        "plan_update" => "Soll ich diese Planänderung jetzt final in Kura speichern?",
        "preference" | "profile" => "Soll ich diese Einstellungen jetzt final in Kura speichern?",
        "observation" => "Soll ich diese Beobachtung jetzt final in Kura speichern?",
        _ => "Soll ich diese Ergebnisse jetzt final in Kura speichern?",
    };
    Some(prompt.to_string())
}

fn build_draft_scope(event: &CreateEventRequest) -> Value {
    let mut scope = serde_json::Map::new();
    scope.insert("level".to_string(), Value::String("session".to_string()));
    if let Some(session_id) = event
        .metadata
        .session_id
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        scope.insert(
            "session_id".to_string(),
            Value::String(session_id.to_string()),
        );
    }
    if let Some(exercise_id) = event
        .data
        .get("exercise_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        scope.insert(
            "exercise_id".to_string(),
            Value::String(exercise_id.to_lowercase()),
        );
    }
    Value::Object(scope)
}

fn summarize_event_value(event: &CreateEventRequest) -> Value {
    let mut summary = serde_json::Map::new();
    summary.insert(
        "event_type".to_string(),
        Value::String(event.event_type.trim().to_lowercase()),
    );

    let Some(data_obj) = event.data.as_object() else {
        summary.insert("data".to_string(), event.data.clone());
        return Value::Object(summary);
    };

    let preferred_keys = [
        "exercise",
        "exercise_id",
        "reps",
        "weight_kg",
        "duration_seconds",
        "distance_meters",
        "context_text",
        "notes",
        "summary",
        "change_scope",
        "volume_delta_pct",
        "intensity_delta_pct",
    ];
    for key in preferred_keys {
        if let Some(value) = data_obj.get(key) {
            summary.insert(key.to_string(), value.clone());
        }
    }
    if summary.len() == 1 {
        for (key, value) in data_obj.iter().take(6) {
            summary.insert(key.to_string(), value.clone());
        }
    }
    Value::Object(summary)
}

fn draft_confidence(
    claim_guard: &AgentWriteClaimGuard,
    session_audit: &AgentSessionAuditSummary,
) -> f64 {
    let mut confidence: f64 = match claim_guard.claim_status.as_str() {
        "pending" => 0.72_f64,
        "failed" => 0.55_f64,
        _ => 0.65_f64,
    };
    if claim_guard
        .uncertainty_markers
        .iter()
        .any(|marker| marker == "write_receipt_incomplete")
    {
        confidence = confidence.min(0.58);
    }
    if session_audit.status == "needs_clarification" {
        confidence = confidence.min(0.60);
    }
    round_to_two(confidence.clamp(0.0, 1.0))
}

fn build_draft_events(
    user_id: Uuid,
    events: &[CreateEventRequest],
    receipts: &[AgentWriteReceipt],
    mode: &str,
    reason_codes: &[String],
    claim_guard: &AgentWriteClaimGuard,
    session_audit: &AgentSessionAuditSummary,
) -> Vec<CreateEventRequest> {
    let mut drafts = Vec::new();
    let mut seen_idempotency_keys: HashSet<String> = HashSet::new();
    let confidence = draft_confidence(claim_guard, session_audit);

    for (index, event) in events.iter().enumerate() {
        let Some(topic) = persist_topic_for_event(event) else {
            continue;
        };
        let event_type = event.event_type.trim().to_lowercase();
        let summary_value = summarize_event_value(event);
        let summary_fingerprint =
            serde_json::to_string(&summary_value).unwrap_or_else(|_| "{}".to_string());
        let source_receipt = receipts.get(index);
        let source_event_id = source_receipt.map(|receipt| receipt.event_id.to_string());

        let context_text = event_text_candidates(event)
            .into_iter()
            .next()
            .map(str::to_string)
            .or_else(|| {
                Some(format!(
                    "Draft aus {} wegen persist_intent={} (claim_status={}).",
                    event_type, mode, claim_guard.claim_status
                ))
            })
            .unwrap_or_default();

        let seed = format!(
            "{}|{}|{}|{}|{}|{}|{}",
            user_id,
            event.metadata.idempotency_key,
            event_type,
            topic,
            mode,
            claim_guard.claim_status,
            summary_fingerprint
        );
        let idempotency_key = format!("persist-intent-draft-{}", stable_hash_suffix(&seed, 20));
        if !seen_idempotency_keys.insert(idempotency_key.clone()) {
            continue;
        }

        let mut provenance = serde_json::Map::new();
        provenance.insert(
            "source_type".to_string(),
            Value::String("inferred".to_string()),
        );
        provenance.insert(
            "source_path".to_string(),
            Value::String("agent_write_with_proof.persist_intent".to_string()),
        );
        provenance.insert(
            "source_event_type".to_string(),
            Value::String(event_type.clone()),
        );
        provenance.insert(
            "reason_codes".to_string(),
            Value::Array(
                reason_codes
                    .iter()
                    .map(|code| Value::String(code.clone()))
                    .collect(),
            ),
        );
        if let Some(event_id) = source_event_id {
            provenance.insert("source_event_id".to_string(), Value::String(event_id));
        }

        drafts.push(CreateEventRequest {
            timestamp: Utc::now(),
            event_type: "observation.logged".to_string(),
            data: serde_json::json!({
                "dimension": format!("provisional.persist_intent.{topic}"),
                "value": summary_value,
                "context_text": context_text,
                "tags": [
                    "persist_intent",
                    format!("mode:{mode}"),
                    format!("claim_status:{}", claim_guard.claim_status),
                ],
                "confidence": confidence,
                "provenance": Value::Object(provenance),
                "scope": build_draft_scope(event),
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

    drafts
}

pub(super) fn build_persist_intent_computation(
    user_id: Uuid,
    events: &[CreateEventRequest],
    receipts: &[AgentWriteReceipt],
    verification: &AgentWriteVerificationSummary,
    claim_guard: &AgentWriteClaimGuard,
    session_audit: &AgentSessionAuditSummary,
    action_class: &str,
) -> PersistIntentComputation {
    let grouped = grouped_items(events);
    let mut reason_codes: Vec<String> = Vec::new();
    let mut mode = if claim_guard.allow_saved_claim {
        "auto_save".to_string()
    } else {
        "auto_draft".to_string()
    };

    if verification.status == "pending" {
        push_reason_code(&mut reason_codes, "verification_pending");
    } else if verification.status == "failed" {
        push_reason_code(&mut reason_codes, "verification_failed");
    }
    if !claim_guard.allow_saved_claim {
        push_reason_code(&mut reason_codes, "claim_guard_unsaved");
    }
    if session_audit.status == "needs_clarification" {
        push_reason_code(&mut reason_codes, "session_audit_needs_clarification");
    }
    if claim_guard.autonomy_gate.decision == "confirm_first" {
        push_reason_code(&mut reason_codes, "autonomy_gate_confirm_first");
    }
    if action_class == "high_impact_write" {
        push_reason_code(&mut reason_codes, "high_impact_write");
    }

    let save_mode = normalize_save_confirmation_mode_override(Some(
        claim_guard.autonomy_policy.save_confirmation_mode.as_str(),
    ))
    .unwrap_or_else(|| "auto".to_string());
    match save_mode.as_str() {
        "always" => {
            if claim_guard.allow_saved_claim {
                push_reason_code(
                    &mut reason_codes,
                    "user_save_confirmation_mode_always_observed_after_save",
                );
            } else {
                mode = "ask_first".to_string();
                push_reason_code(&mut reason_codes, "user_save_confirmation_mode_always");
            }
        }
        "never" => {
            push_reason_code(&mut reason_codes, "user_save_confirmation_mode_never");
            if !claim_guard.allow_saved_claim {
                mode = "auto_draft".to_string();
            }
        }
        _ => {}
    }

    if !claim_guard.allow_saved_claim
        && (action_class == "high_impact_write"
            || claim_guard.autonomy_gate.decision == "confirm_first")
    {
        mode = "ask_first".to_string();
        push_reason_code(&mut reason_codes, "safety_floor_confirm_first");
    }

    if reason_codes.len() > MAX_REASON_CODES {
        reason_codes.truncate(MAX_REASON_CODES);
    }

    let draft_required =
        !claim_guard.allow_saved_claim && (mode == "auto_draft" || mode == "ask_first");
    let draft_events = if draft_required {
        build_draft_events(
            user_id,
            events,
            receipts,
            &mode,
            &reason_codes,
            claim_guard,
            session_audit,
        )
    } else {
        Vec::new()
    };

    let mut status_label = if claim_guard.allow_saved_claim {
        "saved".to_string()
    } else {
        "not_saved".to_string()
    };
    if !claim_guard.allow_saved_claim && !draft_events.is_empty() {
        status_label = "draft".to_string();
    }

    let user_prompt = if mode == "ask_first" && !claim_guard.allow_saved_claim {
        build_single_confirmation_prompt(&grouped)
    } else {
        None
    };

    PersistIntentComputation {
        persist_intent: AgentPersistIntent {
            schema_version: PERSIST_INTENT_SCHEMA_VERSION.to_string(),
            mode,
            status_label,
            reason_codes,
            grouped_items: grouped,
            user_prompt,
            draft_event_count: draft_events.len(),
            draft_persisted_count: 0,
        },
        draft_events,
    }
}
