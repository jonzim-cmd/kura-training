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

### Phase 1: Graceful Degradation (Innate Immunity) ✅

**Goal:** Nothing gets silently dropped. Handlers become tolerant.

**Implemented (2026-02-09):**
- `separate_known_unknown(data, known_fields)` utility in `utils.py`
- `merge_observed_attributes(accumulator, event_type, unknown)` for frequency tracking
- `check_expected_fields(data, expected)` for missing field hints
- All 5 data handlers (exercise_progression, training_timeline, body_composition, recovery, nutrition) split known/unknown
- Unknown fields stored as `observed_attributes` in each handler's `data_quality`
- Backward compatible: existing projections gained `observed_attributes`, nothing else changed

### Phase 2: Pattern Detection (Adaptive Immunity) ✅

**Goal:** System observes — no thresholds, everything surfaced immediately.

**Implemented (2026-02-10):**
- `observed_attributes` event_type-aware: `{event_type: {field: count}}` in every handler's `data_quality`
- `observed_patterns` in user_profile/me: cross-dimension merge of observed fields + orphaned event type field analysis
- Agenda items with priority `info`: `field_observed` and `orphaned_event_type`
- Datagen `--novel-fields` flag: profile-specific novel fields + orphaned event types (supplement.logged, cardio.logged)
- No thresholds — frequency is metadata, not a gate. The agent decides what's relevant.

**Key insight from implementation:** The observation landscape revealed three distinct patterns that Phase 3 must handle:
1. **Simple numeric tracking** — standalone fields (hrv_rmssd, fiber_g, stress_level) → "show me the trend over time"
2. **Categorized tracking** — orphaned event types with a natural grouping key (supplements by name, cardio by type)
3. **Contextual fields** — fields meaningful only within their parent event's context (tempo, rest_seconds, bar_speed in set.logged need exercise + weight context)

### Phase 3: Agent-Mediated Evolution (Epigenetics)

**Goal:** Agent creates projection rules. System adapts to each user.

**Key design insight (from Phase 2 implementation):** Rules are simple declarations, not a DSL. The intelligence is in the agent's DECISION to create a rule, not in the rule itself. Like epigenetics: the signal says "express this gene," the cell does the complex work.

**Agent autonomy:** The agent decides autonomously to create rules based on observed_patterns. It informs the user naturally ("I've started tracking your HRV trends") instead of asking technical questions ("Should I build a trend?"). Rules are inspectable and revocable — cheap to undo if wrong. Agent behavior scope levels (strict/moderate/proactive) govern how autonomous the agent acts.

#### Three Rule Patterns

**Pattern 1: Field Tracking** — standalone numeric fields from known event types.
Agent sees `hrv_rmssd` 90× in sleep.logged → creates rule → engine builds time series.
```json
{
  "name": "hrv_tracking",
  "type": "field_tracking",
  "source_events": ["sleep.logged"],
  "fields": ["hrv_rmssd", "deep_sleep_pct"]
}
```
Engine applies standard projection logic: recent values (30), daily, weekly averages, all-time stats, anomaly detection. Same patterns as core handlers — no new algorithms needed.

**Pattern 2: Categorized Tracking** — orphaned event types with a natural grouping key.
Agent sees 360 supplement.logged events → creates rule → engine builds per-category projection.
```json
{
  "name": "supplement_tracking",
  "type": "categorized_tracking",
  "source_events": ["supplement.logged"],
  "fields": ["name", "dose_mg", "timing"],
  "group_by": "name"
}
```
Engine groups events by the group_by field, then builds per-group time series.

**Pattern 3: Contextual Fields** — fields meaningful only within their parent event's context (tempo, rest_seconds, bar_speed within set.logged need exercise + session context). **Deferred** — these are best handled by extending existing core handlers, not by standalone rules. Design TBD after Pattern 1+2 validation.

#### Implementation

**Events (event-sourced rules):**
- `projection_rule.created` — rule data as event payload
- `projection_rule.archived` — deactivates a rule

**New handler:** `custom_projection` in workers
- Subscribes to `projection_rule.created`, `projection_rule.archived`
- On rule event: reads all active rules for user, recomputes matching projections
- Full replay per rule (idempotent, same as core handlers)

**Router extension:** After normal handler dispatch, router checks for active custom projection rules matching the event_type. Covers two cases:
- Known event types (sleep.logged) where a field_tracking rule extracts novel fields
- Orphaned event types (supplement.logged) that previously had no handler at all

**Projection output:**
- projection_type: `custom`
- key: rule name (e.g., `hrv_tracking`)
- data: standard structure (recent_entries, weekly_average, all_time, data_quality)

**API:** Agent posts rule events via normal `POST /v1/events`. No dedicated endpoint needed initially — the events API is the universal interface.

**Core handlers remain** for complex logic: Epley 1RM, alias resolution, session grouping, anomaly detection. Custom rules cover simple extraction + aggregation.

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
