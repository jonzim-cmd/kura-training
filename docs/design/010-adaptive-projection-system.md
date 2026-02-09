# Decision 10: Adaptive Projection System

**Status:** Accepted (2026-02-09)
**Context:** Kura-Playtest revealed that handlers are rigid — unknown fields are silently dropped, the system can't adapt to individual users.

## Problem

The system is binary: handler exists → full processing, handler doesn't exist → data invisible to agent. No spectrum between "fully understood" and "not understood at all".

Events are flexible (free-form JSONB), but that flexibility is lost as data moves through handlers into projections. The agent reads projections — the most rigid layer.

## Biological Analogy

| Biology | Kura |
|---------|------|
| DNA (immutable, complete) | Event Store |
| Gene Expression (context-dependent) | Projections |
| Epigenetics (rules that change based on environment) | Projection Rules |
| Innate Immunity (handles known patterns) | Core Handlers (coded, complex logic) |
| Adaptive Immunity (learns from exposure) | Pattern Detection (new event types, recurring fields) |
| Memory B-Cells (learned patterns persist) | Learned projection rules |
| Hebbian Learning (fire together → wire together) | Repeated patterns → suggested dimensions |

The key insight: the agent is the **epigenetic signal**. It determines what gets expressed from the event store, adapting to each user.

## Phased Implementation

### Phase 1: Graceful Degradation (Innate Immunity)

**Goal:** Nothing gets silently dropped. Handlers become tolerant.

Each handler defines `KNOWN_FIELDS` — the fields it actively processes. All other fields from the event are stored as `observed_attributes` in the projection. When expected fields are missing (e.g., no `weight_kg` on a set), `data_quality` signals this as a question, not an error.

**Implementation:**
- Utility function `separate_known_unknown(data, known_fields)` in `utils.py`
- Each handler calls this; unknown fields stored per-set/per-event in projection
- `data_quality` gets new hint type: `missing_expected_field` with message like "weight_kg missing — bodyweight exercise?"
- Backward compatible: existing projections gain `observed_attributes`, nothing else changes

**Scope:** Modify all 7 handlers + add utility function.

### Phase 2: Pattern Detection (Adaptive Immunity)

**Goal:** System observes and suggests.

Extend `orphaned_event_types` (already in user_profile) to also track:
- **Recurring unknown fields:** If `duration_sec` appears 5+ times across events, flag it
- **Agent query patterns:** If the agent repeatedly calls `GET /v1/events` with type filters (because projections don't cover it), log this as a signal
- Surface in `agenda`: "Recurring field `band_strength` detected in pull_up sets. Consider creating a tracking rule."

**Implementation:**
- New section in user_profile projection: `observed_patterns`
- Worker tracks field frequency across events per user
- Agenda item generation when patterns cross threshold

### Phase 3: Agent-Mediated Evolution (Epigenetics)

**Goal:** Agent creates projection rules. System adapts to each user.

The agent writes declarative projection rules — not code, not SQL:

```json
{
  "name": "band_progression",
  "source_events": ["set.logged"],
  "filter": {"exercise_id": "pull_up", "has_field": "band_strength"},
  "extract": ["band_strength", "reps", "set_number"],
  "group_by": "week",
  "track": "latest_per_group"
}
```

A sandboxed execution engine processes these rules. The agent writes config, the engine executes only allowed operations (filter, extract, group, simple aggregation). Complex logic (Epley 1RM, alias resolution) stays in coded core handlers.

**Implementation:**
- New event type: `projection_rule.created`, `projection_rule.updated`, `projection_rule.archived`
- New handler: `custom_projection` — reads rules, applies them to events
- API: `POST /v1/projection-rules` (agent-facing)
- Sandboxed engine with whitelist of operations
- Core handlers remain for complex logic (~30% of cases)
- Declarative rules cover simple extraction + aggregation (~70% of cases)

### Phase 4: Cross-Pollination (Immune Memory)

**Goal:** Learned patterns become shared templates.

- Rules that work for one user become a template library
- New users get suggested rules based on their activity patterns
- "Other users doing olympic lifting track: band_progression, time_under_tension, ..."
- Optional: naming normalization across users for cross-pollination

**Implementation:** Future. Depends on multi-user patterns.

## Degradation Spectrum

After all phases:

| Level | What happens | Phase |
|-------|-------------|-------|
| 0 | Event stored | Already works |
| 1 | Event accessible as raw data | Already works (GET /v1/events) |
| 2 | Unknown attributes visible in projections | Phase 1 |
| 3 | System detects patterns and suggests dimensions | Phase 2 |
| 4 | Agent creates custom projection rules | Phase 3 |
| 5 | Full coded handler (complex logic) | Already works |

## Security Model (Phase 3)

Agent-created rules are declarative, not executable code. The engine:
- Only performs whitelisted operations (filter, extract, group, count, sum, avg, min, max, latest)
- Cannot access other users' data (RLS still applies)
- Cannot modify events (read-only by design)
- Rules are inspectable: `GET /v1/projection-rules` returns all active rules
- Rules are revocable: agent or user can archive rules

## Design Principles

1. **Nothing is silently dropped** — from Phase 1 onward, all data is accessible
2. **Suggest, don't assume** — system proposes, agent/user decides
3. **Inspectable** — all rules are transparent, no black-box behavior
4. **Graceful** — each phase works independently, no big-bang migration
5. **Agent is the epigenetic signal** — the agent adapts the system to the user
