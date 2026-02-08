# Design 002: Data Model, Agent Interface & Onboarding

Status: **Decided** (2026-02-08)

## Context

Kura has a working Event Store, Auth, and Projection Infrastructure. The next
question: how does messy real-world training data become structured, useful
events? The agent (LLM) is the primary interface — it parses user input,
normalizes data, and writes structured events. But the agent needs persistent
context to do this well.

## Core Principle: The Agent Normalizes, Kura Remembers

The LLM is the intelligence layer. It understands "Kniebeuge" = "squat",
handles ambiguity, asks clarifying questions. But session context dies when
the conversation ends. Kura's job is to persist the knowledge the agent
gains — aliases, preferences, vocabulary — so any agent, any session, any
time can pick up where the last one left off.

```
User Input
  |
Agent (LLM)
  |-- [1] Read user_profile projection -> know aliases, preferences, context
  |-- [2] Parse user input -> recognize exercises, values, intention
  |-- [3] Resolve exercise -> alias lookup, or ask user to confirm
  |-- [4] Structure event(s) -> conventions, units, exercise_id
  `-- [5] POST /v1/events/batch -> Kura stores, workers compute projections

User Question
  |
Agent (LLM)
  |-- [1] Read relevant projections
  |-- [2] Contextualize (language, units, user level)
  `-- [3] Answer the user
```

## Decision 1: Per-User Alias & Preference System

Every user builds a personal vocabulary over time. Aliases and preferences
are events like everything else — immutable, versioned, per user.

### Event Types

**`exercise.alias_created`**
```json
{
  "event_type": "exercise.alias_created",
  "data": {
    "alias": "Kniebeuge",
    "exercise_id": "barbell_back_squat",
    "confidence": "confirmed"
  }
}
```
- `confidence`: "confirmed" (user confirmed) or "inferred" (agent guessed)
- Many-to-one: "Kniebeuge", "SQ", "Squats" -> all map to same exercise_id
- Any agent working with this user inherits the vocabulary

**`preference.set`**
```json
{
  "event_type": "preference.set",
  "data": {
    "key": "unit_system",
    "value": "metric"
  }
}
```
- Keys: `unit_system` (metric/imperial), `language`, `default_rpe_scale`, etc.
- Latest event per key wins (projection rebuilds from all preference events)

**`goal.set`**
```json
{
  "event_type": "goal.set",
  "data": {
    "goal_type": "strength",
    "target_exercise": "barbell_back_squat",
    "target_1rm_kg": 140,
    "timeframe_weeks": 12
  }
}
```

### User Profile Projection

A meta-projection that answers: "What does Kura know about this user?"

```json
{
  "projection_type": "user_profile",
  "key": "me",
  "data": {
    "exercises_logged": ["barbell_back_squat", "barbell_bench_press"],
    "aliases": {
      "Kniebeuge": "barbell_back_squat",
      "SQ": "barbell_back_squat",
      "Bankdruecken": "barbell_bench_press"
    },
    "preferences": {
      "unit_system": "metric",
      "language": "de"
    },
    "goals": [...],
    "total_sessions": 47,
    "total_events": 312,
    "first_event": "2025-06-15",
    "last_event": "2026-02-08",
    "available_projections": ["exercise_progression", "session_summary"]
  }
}
```

One call. Agent knows everything it needs from the first message.

## Decision 2: Conventions for set.logged (Not Schemas)

No hardcoded schemas, no enums. Documented conventions that agents follow.
Projections take what's there and handle missing fields gracefully.

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `exercise` | Yes | Free text, what the user/agent said. For provenance. |
| `exercise_id` | No | Canonical ID (e.g. `barbell_back_squat`). For projections. |
| `weight_kg` | No* | Weight in kg. Unit in field name eliminates ambiguity. |
| `reps` | No* | Repetition count. |
| `rpe` | No | Rate of perceived exertion (1-10, half steps allowed). |
| `set_type` | No | warmup, working, amrap, drop, backoff, etc. Free text. |
| `tempo` | No | String like "3-1-2-0" (eccentric-pause-concentric-pause). |
| `duration_seconds` | No* | For timed exercises (planks, carries, etc.). |
| `distance_meters` | No* | For distance exercises (runs, rows, etc.). |
| `rest_seconds` | No | Rest before this set. |
| `notes` | No | Free text notes for this specific set. |

*At least one measurement required (weight_kg+reps, or duration_seconds, or distance_meters).

### Design Decisions

- **Units in field names** (`weight_kg`, `distance_meters`, `duration_seconds`).
  No separate unit field needed. The agent converts at write time.
- **`exercise` + `exercise_id` duality**: `exercise` is always set (provenance).
  `exercise_id` is set when the agent is confident about the canonical mapping.
  Projections prefer `exercise_id`, fall back to normalized `exercise`.
- **No validation at API level** for field contents. The projection handlers
  are lenient — they take what's there. This keeps the system flexible for
  new exercise types, metrics, etc.

### Validation Philosophy

The API validates **structure** (valid JSON, non-empty event_type, valid user)
but not **content** (field types, value ranges, required fields within `data`).

Why:
- The agent is the intelligence layer. It reads conventions, structures data
  correctly. If it sends bad data, that's an agent bug — not an API concern.
- API-level validation couples the API to event types. New event types should
  work without API changes.
- Imports (Garmin, Excel, legacy data) may not conform to conventions.
  The API accepts them; projections handle them gracefully.
- Event data is immutable. But the solution to bad data is visibility, not
  rejection: handlers skip invalid records with warnings. A future
  `data_quality` projection will surface these issues to the agent.

### Projection Coverage by Field

Not every field is consumed by every projection. Fields are stored in events
and available for current and future projections.

| Field | Used by | Notes |
|-------|---------|-------|
| `exercise`, `exercise_id` | exercise_progression, user_profile | Core identity fields |
| `weight_kg`, `reps` | exercise_progression | Strength exercises: 1RM, volume, PRs |
| `rpe`, `set_type` | exercise_progression (recent_sessions) | Context in output |
| `duration_seconds` | *future: timed_progression* | Planks, carries, holds |
| `distance_meters` | *future: activity_progression* | Runs, rows, swims |
| `tempo` | *future* | Stored, not projected yet |
| `rest_seconds` | *future* | Stored, not projected yet |
| `notes` | *future* | Stored, not projected yet |

Different exercise types need different progression logic. Strength (weight x reps)
is fundamentally different from endurance (distance / time) or isometric (duration).
Each gets its own projection handler when needed.

## Decision 3: Session Grouping

Sessions are a grouping concept, not a first-class entity. Sets belonging
to the same session share a `session_id` in their metadata.

Optional `session.started` and `session.ended` events can bracket a session
for additional context (duration, location, program phase, overall RPE, notes).
But they're not required — sets can exist without a session envelope.

## Decision 4: Onboarding Interview

Inspired by [Anthropic Interviewer](https://www.anthropic.com/research/anthropic-interviewer).

When a new user connects for the first time, the agent conducts a structured
but adaptive interview. Not pre-built questions — the agent reacts to context
and adapts. The interview produces events that bootstrap the user's profile.

### What the Interview Establishes

- Training history (duration, type, experience level)
- Current program (if any)
- Goals (strength, hypertrophy, endurance, weight loss, health)
- Exercise vocabulary (what they call their exercises)
- Unit preferences (kg/lbs, km/miles)
- Language
- Injuries / limitations
- Available equipment
- Training frequency and schedule
- Nutrition tracking interest (yes/no/later)

### Output: Events

Every piece of information becomes an event:
- `preference.set` (units, language)
- `exercise.alias_created` (vocabulary mapping)
- `goal.set` (training goals)
- `injury.reported` (current injuries)
- `profile.updated` (experience level, training frequency)

After the interview, `user_profile` projection is populated. Every future
interaction is contextualized from the start.

### Key Design Principle

The interview is conducted by the agent (LLM), not by Kura. Kura doesn't
need interview logic — it receives the structured events that result from
the interview. Any agent can conduct the interview, in any language, in
any style. The output format (events) is standardized.

## Decision 5: Dimensions, Not Answers

### The Problem

We cannot predict every question a user will ask. A user might log a workout,
ask for a suggestion, ask why they're stagnating, or request a training plan.
Building projections around specific questions ("session summary", "weekly
report") creates a rigid system that breaks whenever reality doesn't match
our assumptions.

### The Solution: Kura describes what IS. The agent decides what to DO.

Projections are **dimensions** — orthogonal views of the data that the agent
composes freely to answer any question. Each dimension covers a data axis,
not a use case.

### Core Dimensions

| Dimension | Key | What it provides |
|-----------|-----|------------------|
| `user_profile` | `me` | Identity + Manifest of all available dimensions |
| `exercise_progression` | per exercise | Current state + weekly time series per exercise |
| `training_timeline` | `overview` | Per-day/week aggregates: volume, exercises, frequency |

Future dimensions (nutrition, sleep, body composition, Bayesian posteriors)
follow the same pattern. Each is independent, each registers in the manifest.

### The Manifest Pattern

`user_profile` is the agent's entry point — not just "who is this user" but
"what does Kura know about this user." It includes a `dimensions` section
that describes every available dimension: what it covers, how fresh it is,
where the gaps are.

```json
{
  "identity": {
    "aliases": {"Kniebeuge": "barbell_back_squat"},
    "preferences": {"unit_system": "metric", "language": "de"},
    "goals": [...]
  },
  "dimensions": {
    "exercise_progression": {
      "exercises": ["barbell_back_squat", "barbell_bench_press"],
      "coverage": {"from": "2025-06-15", "to": "2026-02-08"},
      "freshness": "2026-02-08T14:30:00Z"
    },
    "training_timeline": {
      "weeks_tracked": 34,
      "last_training": "2026-02-07",
      "freshness": "2026-02-08T14:30:00Z"
    }
  },
  "data_quality": {
    "events_without_exercise_id": 12,
    "unresolved_exercises": ["that weird cable thing"]
  }
}
```

One call. The agent knows everything that's available. Then it decides —
based on the user's actual question — which dimensions to read in detail.

### Why This Works

The agent brings general intelligence. Kura brings persistent context.
A specialized model (like Anthropic Interviewer) is trained for one task
and excels at it. A general agent with perfect context can handle ANY task —
because it composes context into answers on the fly.

Kura doesn't need to anticipate questions. It needs to organize knowledge
so that the agent can find and compose it efficiently. The manifest tells
the agent what's available. The dimensions provide the data. The agent
does the thinking.

### Design Rules for Dimensions

1. **Dimensions describe data axes, not use cases.** "Exercise over time" not
   "weekly report." "Training patterns" not "am I consistent?"
2. **Every dimension registers in the manifest.** New handler → manifest update.
   The agent discovers new dimensions automatically.
3. **Dimensions are composable.** exercise_progression + training_timeline =
   "am I progressing AND am I consistent?" No dimension assumes it's read alone.
4. **Time granularity is built in.** A dimension provides day/week/month views
   in one projection, not three separate projections.
5. **Raw events remain accessible.** For edge cases no dimension covers, the
   agent falls back to `GET /v1/events` with filters.

## Decision 6: Three Layers of Intelligence (unchanged)

```
LLM (Agent)     -> Understanding, communication, clarification, context
ML (Backend)    -> Pattern matching, classification, embeddings, batch processing
Statistics      -> Analysis, Bayesian inference, trends, predictions (PyMC/Stan)
```

### Where ML fits (not LLM, not statistics)

1. **Embeddings (pgvector)**: Pre-trained sentence transformers for semantic
   similarity. "Kniebeuge" and "barbell back squat" are close in embedding
   space. Works across languages natively. No custom training needed.

2. **Background classification**: When the agent isn't in the loop (imports,
   batch processing), small classifiers for:
   - Exercise -> muscle group mapping
   - Exercise type inference (strength/cardio/flexibility)
   - Movement pattern classification (push/pull/hinge/squat/carry)

3. **Cross-user pattern recognition**: The agent sees one user. Backend ML
   sees all users. "90% of users who write 'RDL' mean Romanian Deadlift."

### Principle

The agent handles interactive normalization (and is better at it than any
small model). Backend ML handles batch processing and cross-user patterns
where the agent isn't present. Neither replaces the other.

## Implementation Priority

1. ~~**Preference/Alias event types + user_profile projection**~~ ✅ Done.
2. ~~**set.logged conventions documented + exercise_progression updated**~~ ✅ Done.
3. ~~**Dimension architecture**~~ ✅ Done.
   - `exercise_progression` extended with `weekly_history` (26 weeks)
   - `training_timeline` dimension built (recent_days, weekly_summary, frequency, streak)
   - `user_profile` evolved into manifest (dimensions discovery, data_quality)
4. **Onboarding interview design** — agent-side, produces standard events.
5. **Semantic layer (pgvector + embeddings)** — background resolution,
   cross-language matching, fuzzy alias suggestions.

## Accepted Trade-offs (Dimension Architecture)

Reviewed 2026-02-08. These are conscious decisions, not oversights.

### Router transaction isolation (per-handler, not atomic)

Each projection handler runs in its own transaction. If one handler fails,
the others still complete and the job is marked done. This means a failed
handler's projection can be temporarily stale until the next event triggers
a rebuild. We accept this because:
- Full recompute on every event is self-healing — the next event fixes it.
- Atomic all-or-nothing would mean one broken handler blocks all projections.
- At current scale, the risk is low. Revisit when we add monitoring/alerting.

### Flat user_profile structure (no `identity` wrapper)

The design doc shows `identity: { aliases, preferences, goals }` as a wrapper.
Implementation uses a flat structure with these fields at the top level.
The flat version is simpler for the agent — fewer nesting levels to navigate.
If the manifest grows significantly, we may introduce grouping later.

### Alias `confidence` field not stored in projection

`exercise.alias_created` events have a `confidence` field ("confirmed" /
"inferred"), but the user_profile projection only stores alias → target
mapping. The confidence information is preserved in the events and can be
surfaced when we need to distinguish confirmed from inferred aliases
(e.g., for the semantic layer's fuzzy suggestions).

### training_timeline only covers `set.logged`

The handler name suggests broader coverage, but it only processes `set.logged`
events. `activity.logged` (runs, swims) and other training event types are
not yet defined as conventions. When they are, training_timeline will be
extended to include them. Until then, the scope is intentionally limited.
