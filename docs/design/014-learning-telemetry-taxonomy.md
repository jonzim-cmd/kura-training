# Design 014: Learning Telemetry Taxonomy

Status: implemented (2026-02-11)

## Goal

Define a canonical telemetry schema for implicit learning signals so quality
defects, repair behavior, save-friction, and correction outcomes are
consistently clusterable across users, sessions, and agent/runtime versions.

## Canonical Event

`event_type`: `learning.signal.logged`

```json
{
  "schema_version": 1,
  "signal_type": "save_claim_mismatch_attempt",
  "category": "friction_signal",
  "captured_at": "2026-02-11T12:12:00Z",
  "user_ref": {
    "pseudonymized_user_id": "u_7ac3f5be2ab8d93e55f1f8c3"
  },
  "signature": {
    "issue_type": "save_claim_mismatch_attempt",
    "invariant_id": "INV-002",
    "agent_version": "api_agent_v1",
    "workflow_phase": "agent_write_with_proof",
    "modality": "chat",
    "confidence_band": "medium"
  },
  "cluster_signature": "ls_40a2cb4d2f5e6f2443e0",
  "attributes": {
    "requested_event_count": 2,
    "receipt_count": 2,
    "verification_status": "pending"
  }
}
```

## Categories

- `quality_signal`
- `friction_signal`
- `outcome_signal`
- `correction_signal`

## Core Signal Types

1. `quality_issue_detected`
2. `repair_proposed`
3. `repair_simulated_safe`
4. `repair_simulated_risky`
5. `repair_auto_applied`
6. `repair_auto_rejected`
7. `repair_verified_closed`
8. `save_handshake_verified`
9. `save_handshake_pending`
10. `save_claim_mismatch_attempt`
11. `workflow_violation`
12. `workflow_phase_transition_closed`
13. `workflow_override_used`
14. `viz_shown`
15. `viz_skipped`
16. `viz_source_bound`
17. `viz_fallback_used`
18. `viz_confusion_signal`
19. `correction_applied`
20. `correction_undone`
21. `clarification_requested`

## Privacy and Pseudonymization

- Cross-user telemetry payloads MUST use `user_ref.pseudonymized_user_id`.
- Raw `user_id` is not included in telemetry payload data.
- Pseudonymization uses a salted deterministic hash so aggregation remains stable.

## Touchpoints

- API save handshake (`/v1/agent/write-with-proof`)
- Quality health detection/proposal/simulation/apply/verify cycle
- Future correction + clarification flows (same schema, same categories)
