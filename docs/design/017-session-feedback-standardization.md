# Design 017: Session Feedback Standardization (`session.completed`)

Status: implemented (2026-02-11)

## Goal

Promote `session.completed` from orphaned free-form logging to a first-class event + projection,
so coaching/planning can adapt using subjective post-session outcomes.

## Event Convention

Canonical event: `session.completed`

Core fields:
- `enjoyment` (1..5)
- `perceived_quality` (1..5)
- `perceived_exertion` (1..10)
- `pain_discomfort` (0..10)
- `pain_signal` (bool/string)
- `context` / `notes` / `summary` (free text)

Metadata:
- `session_id` recommended for alignment with `set.logged` session load.

Backward compatibility:
- Legacy `session.completed` payloads with text-only fields remain supported.
- Normalization infers structured scores from legacy text where deterministic and safe.

## Projection

New projection: `session_feedback/overview`

Outputs:
- Recent normalized feedback entries per session
- Trend rollups (enjoyment/quality/exertion/pain)
- `enjoyment_trend` status
- `load_to_enjoyment_alignment` correlation/status
- Data-quality unknown field tracking

## Agent Integration

- `session_feedback` is included in `/v1/agent/context`.
- Event simulation maps `session.completed` and `set.corrected` impacts to `session_feedback`.
- `user_profile` no longer treats `session.completed` as orphan when normal flow is used.

## Decision-13 Alignment

- Preserves event immutability and correction overlays (`set.corrected` + retraction-aware replay).
- Maintains backward compatibility while improving deterministic, production-oriented coaching context.
