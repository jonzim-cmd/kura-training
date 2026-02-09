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
    "available_projections": ["exercise_progression", "training_timeline"]
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

## Decision 7: The Agent's Entry Point — Three Layers in One Call

### The Problem

The agent's first action in any conversation is: understand the user and the
system. With Decision 5, user_profile became a manifest. But the manifest has
gaps:

1. **Race condition.** user_profile discovers dimensions by querying the
   projections table. On the first-ever event, other handlers haven't written
   yet. The manifest is incomplete.
2. **No navigation.** The manifest lists dimensions but not how they relate.
   With 3 dimensions the agent's world knowledge suffices. With 15 it won't.
3. **No proactivity.** The manifest describes what IS but not what the agent
   SHOULD DO. Data quality problems, unconfirmed aliases, goals at risk —
   the system sees these but doesn't surface them as actionable items.
4. **Missing context for decisions.** `last_updated` tells when the projection
   was rebuilt, not when the user last trained. The agent can't decide which
   dimensions to read in detail without reading them first.

### The Solution: Three Layers

One call (`GET /v1/projections/user_profile/me`) returns three layers:

#### Layer 1: `system` — What Kura Can Do (Declaration)

Static capabilities, identical for all users. Changes only on deployment.
Declared by handlers at registration time, not discovered from data.

```json
"system": {
  "dimensions": {
    "exercise_progression": {
      "description": "Strength progression per exercise over time",
      "key_structure": "one per exercise (exercise_id as key)",
      "granularity": ["set", "week"],
      "event_types": ["set.logged"],
      "relates_to": {
        "training_timeline": {"join": "week", "why": "frequency vs progression"},
        "user_profile": {"join": "exercises_logged", "why": "which exercises to query"}
      }
    },
    "training_timeline": {
      "description": "Training patterns: when, what, how much",
      "key_structure": "single overview per user",
      "granularity": ["day", "week"],
      "event_types": ["set.logged"],
      "relates_to": {
        "exercise_progression": {"join": "week", "why": "volume breakdown per exercise"}
      }
    }
  },
  "time_conventions": {
    "week": "ISO 8601 (2026-W06)",
    "date": "ISO 8601 (2026-02-08)",
    "timestamp": "ISO 8601 with timezone"
  }
}
```

**Why declaration, not observation?** Solves the race condition. The system
layer is built from handler registrations at worker startup, not from
database queries. A dimension is listed the moment its handler is deployed,
even before any data exists.

**Why relationships?** The agent needs a navigation graph, not a flat list.
"If the user asks about Squat progression AND consistency, read
exercise_progression AND training_timeline, join on week." At 3 dimensions
this is obvious. At 15 it's essential.

**Why granularity?** When building new dimensions, forces the question:
"Which granularity levels does this dimension provide?" Prevents gaps like
the missing session level in the initial implementation.

#### Layer 2: `user` — What Kura Knows About This User (Observation)

Dynamic, per-user. Rebuilt on every event. Identity, dimension coverage,
data quality with actionable items.

```json
"user": {
  "aliases": {"Kniebeuge": "barbell_back_squat"},
  "preferences": {"unit_system": "metric", "language": "de"},
  "goals": [{"goal_type": "strength", "target_exercise": "barbell_back_squat", "target_1rm_kg": 140}],
  "exercises_logged": ["barbell_back_squat", "barbell_bench_press"],
  "total_events": 312,
  "first_event": "2025-06-15T10:00:00Z",
  "last_event": "2026-02-08T14:30:00Z",
  "dimensions": {
    "exercise_progression": {
      "status": "active",
      "exercises": ["barbell_back_squat", "barbell_bench_press"],
      "coverage": {"from": "2025-06-15", "to": "2026-02-08"},
      "freshness": "2026-02-08T14:30:00Z"
    },
    "training_timeline": {
      "status": "active",
      "coverage": {"from": "2025-06-15", "to": "2026-02-08"},
      "freshness": "2026-02-08T14:30:00Z",
      "total_training_days": 127,
      "last_training": "2026-02-08"
    }
  },
  "data_quality": {
    "total_set_logged_events": 188,
    "events_without_exercise_id": 12,
    "actionable": [
      {
        "type": "unresolved_exercise",
        "exercise": "that weird cable thing",
        "occurrences": 7
      },
      {
        "type": "unconfirmed_alias",
        "alias": "SQ",
        "target": "barbell_back_squat",
        "confidence": "inferred"
      }
    ]
  }
}
```

**`status`**: `"active"` (has data), `"no_data"` (handler registered but no
projections yet), `"stale"` (freshness > threshold). Comes from merging the
declaration (Layer 1) with actual projection data.

**`coverage`**: Date range of actual data. Not when the projection was rebuilt,
but what time period the data spans. Enough for the agent to decide "do I
need this dimension for a question about last month?"

**`actionable`**: Not passive statistics but a task list for the agent.
"Here are things you can fix right now." Grows as more intelligence layers
come online (semantic suggestions, goal tracking, anomaly detection).

#### Layer 3: `agenda` — What the Agent Should Do (Proactive)

Handlungsaufforderungen. Cross-dimensional pattern recognition that surfaces
opportunities, risks, and maintenance tasks.

```json
"agenda": [
  {
    "priority": "high",
    "type": "goal_at_risk",
    "detail": "Squat 140kg goal in 8 weeks, but weekly_history shows plateau since W03",
    "dimensions": ["exercise_progression", "user_profile"]
  },
  {
    "priority": "medium",
    "type": "resolve_exercises",
    "detail": "7 sets logged as 'that weird cable thing' — suggest canonical name",
    "dimensions": ["user_profile"]
  },
  {
    "priority": "low",
    "type": "confirm_alias",
    "detail": "Alias 'SQ' → barbell_back_squat is inferred, not confirmed",
    "dimensions": ["user_profile"]
  }
]
```

**Implementation phases:**
- **Now:** Data-quality-based items (unresolved exercises, unconfirmed aliases).
  Pure pattern matching over existing data, no ML needed.
- **With Bayesian Engine:** Goal tracking, plateau detection, anomaly alerts.
  Requires statistical compute but fits the same structure.
- **With Semantic Layer:** Exercise suggestions ("that weird cable thing"
  → "cable_crossover" with 92% embedding similarity).

**The principle:** Kura is not just a data store the agent queries. It's a
partner that tells the agent what to pay attention to. Not by talking to the
user — by giving the agent the right impulses.

### Design Rules

1. **One call, full picture.** The agent's first call returns system + user +
   agenda. No second call needed to understand the landscape.
2. **Declaration over observation.** System capabilities come from handler
   registration, not from database queries. No race conditions.
3. **Navigation, not enumeration.** Dimensions declare relationships so the
   agent can traverse a graph, not scan a list.
4. **Actionable, not descriptive.** data_quality and agenda surface things
   the agent should DO, not just things that ARE.
5. **Composable time conventions.** All dimensions that produce time series
   use ISO 8601 week keys (`2026-W06`) and date keys (`2026-02-08`).
   Guaranteed joinable across dimensions.

### Granularity Checklist for New Dimensions

Before building a new dimension, verify which granularity levels it provides:

| Level | Example | Must have? |
|-------|---------|-----------|
| Set / Individual | Single set, single meal, single measurement | If the dimension tracks individual events |
| Session | Training session, daily nutrition | If events are naturally grouped |
| Day | Per-day aggregates | Almost always |
| Week | Weekly summaries, trends | Almost always |
| All time | Totals, records, streaks | Almost always |

A dimension doesn't need all levels. But the question must be asked. The
missing session level in training_timeline was caught by this checklist.

### Technical Implementation

**Registry extension:** `projection_handler` decorator gains an optional
`dimension_meta` parameter:

```python
@projection_handler("set.logged", dimension_meta={
    "name": "exercise_progression",
    "description": "Strength progression per exercise over time",
    "key_structure": "one per exercise (exercise_id as key)",
    "granularity": ["set", "week"],
    "relates_to": {
        "training_timeline": {"join": "week", "why": "frequency vs progression"},
    },
})
async def update_exercise_progression(conn, payload):
    ...
```

**Registry API:** New function `get_dimension_metadata()` returns all
declared dimension metadata. user_profile handler calls this to build
the `system` layer.

**user_profile handler:** Merges declaration (from registry) with observation
(from projections table) to produce the three-layer response. The `system`
layer is static (from registry). The `user` layer enriches dimension entries
with coverage/freshness from actual projection data. The `agenda` layer
runs pattern-matching rules over user data.

## Implementation Priority

1. ~~**Preference/Alias event types + user_profile projection**~~ ✅ Done.
2. ~~**set.logged conventions documented + exercise_progression updated**~~ ✅ Done.
3. ~~**Dimension architecture**~~ ✅ Done.
   - `exercise_progression` extended with `weekly_history` (26 weeks)
   - `training_timeline` dimension built (recent_days, weekly_summary, frequency, streak)
   - `user_profile` evolved into manifest (dimensions discovery, data_quality)
4. ~~**Three-layer entry point** (Decision 7)~~ ✅ Done.
   - `system` / `user` / `agenda` three-layer structure
   - Declaration-based manifest, enriched user state, proactive agenda
5. ~~**Onboarding interview design** (Decision 8)~~ ✅ Done.
   - Interview guide in system layer, context_seeds on dimensions
   - `profile.updated` and `injury.reported` event types
   - Coverage computation, onboarding/refresh agenda triggers
   - Bootstrap response for empty users (Rust endpoint)
   - See `docs/design/008-onboarding-interview.md`
6. **Semantic layer (pgvector + embeddings)** — background resolution,
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

### user_profile structure evolving (Decision 5 → Decision 7)

Decision 5 showed `identity: { aliases, preferences, goals }` as a wrapper.
Initial implementation used a flat structure. Decision 7 defines the target:
`system` / `user` / `agenda` three-layer structure. Current implementation
will be migrated incrementally.

### Alias `confidence` field — stored for actionable items

`exercise.alias_created` events have a `confidence` field ("confirmed" /
"inferred"). Initially not stored in the projection. Decision 7's `agenda`
layer needs confidence to surface "unconfirmed_alias" actionable items.
Implementation will store confidence in the alias map when Decision 7 is
implemented.

## Event Type Conventions: `profile.updated` (Decision 8)

For user attributes gathered during onboarding or updated over time.
Delta merge: later events overwrite earlier per field.

```json
{
  "event_type": "profile.updated",
  "data": {
    "experience_level": "intermediate",
    "training_modality": "strength",
    "training_frequency_per_week": 4,
    "available_equipment": ["barbell", "dumbbells", "rack"],
    "primary_location": "home_gym",
    "current_program": "5/3/1"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `experience_level` | string | beginner, intermediate, advanced |
| `training_modality` | string | strength, endurance, hybrid, crossfit |
| `training_frequency_per_week` | number | Typical sessions per week |
| `available_equipment` | list[string] | Available equipment |
| `primary_location` | string | commercial_gym, home_gym, outdoor |
| `current_program` | string | Program name if applicable |
| `nutrition_tracking` | string | active, not_interested, later |
| `injuries_none` | bool | Explicit "no injuries" flag |

All fields optional. Any subset is valid. Future fields accepted without changes.

## Event Type Conventions: `injury.reported` (Decision 8)

```json
{
  "event_type": "injury.reported",
  "data": {
    "description": "Leichtes Ziehen im linken Knie bei tiefen Squats",
    "affected_area": "knee",
    "severity": "mild"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `description` | Yes | Free text description |
| `affected_area` | No | knee, shoulder, back, hip, etc. |
| `severity` | No | mild, moderate, severe |
| `since` | No | ISO date when injury started |

### training_timeline only covers `set.logged`

The handler name suggests broader coverage, but it only processes `set.logged`
events. `activity.logged` (runs, swims) and other training event types are
not yet defined as conventions. When they are, training_timeline will be
extended to include them. Until then, the scope is intentionally limited.

## Decision 9: Dimension Map & Organic Growth

### The Problem

Decision 5 established that projections are dimensions, not answers. But which
dimensions should exist? We cannot enumerate every possible data axis upfront.
Nor should we — premature dimensions are waste.

Yet some dimensions are clearly needed based on domain knowledge. Training
science, sports medicine, and health tracking define well-established data axes.
We should build those, and let the system tell us when new ones are needed.

### The Dimension Map (Domain Knowledge)

| Dimension | Key | Granularity | Event Types | Status |
|-----------|-----|-------------|-------------|--------|
| `user_profile` | `me` | — | preference.set, exercise.alias_created, goal.set, profile.updated, injury.reported | Implemented |
| `exercise_progression` | per exercise | set, week | set.logged | Implemented |
| `training_timeline` | overview | day, week | set.logged | Implemented |
| `body_composition` | overview | day, week, all-time | bodyweight.logged, measurement.logged | Implemented |
| `recovery` | overview | day, week | sleep.logged, soreness.logged, energy.logged | Implemented |
| `nutrition` | overview | meal, day, week | meal.logged | Implemented |
| `training_plan` | overview | session, week, cycle | training_plan.created/updated/archived | Implemented |
| `activity_progression` | per activity | session, week | activity.logged | When events exist |

Seven dimensions cover all data axes an agent needs for informed coaching.
`activity_progression` follows when endurance/cardio events are conventionalized.

### Three Mechanisms for Dimension Discovery

**1. Domain Knowledge (this decision)**

Sports science defines the axes: performance, volume, frequency, body
composition, recovery, periodization, nutrition. These are the dimensions
above. No ML, no guessing — established science.

**2. Event-Driven Discovery (built-in)**

The Event Store accepts any `event_type`. When users send events that no
handler processes, the system detects this via `orphaned_event_types` in
`data_quality`:

```json
"data_quality": {
  "orphaned_event_types": [
    {"event_type": "mobility.logged", "count": 23}
  ]
}
```

The system surfaces unknown data as a signal: "Consider a new dimension."
Implementation: `user_profile` handler queries all distinct `event_type`
values for a user and compares with `registered_event_types()`.

**3. Agent Pattern Analysis (future)**

When the agent repeatedly computes the same derivation from raw events
across multiple conversations, that's a dimension waiting to crystallize.
Analysis of agent conversation patterns could identify these repeating
computations. This is where ML becomes relevant — not for discovering
dimensions from data, but from agent behavior.

### Event Conventions: body_composition

#### `bodyweight.logged`

```json
{
  "event_type": "bodyweight.logged",
  "data": {
    "weight_kg": 82.5,
    "time_of_day": "morning",
    "conditions": "fasted"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `weight_kg` | Yes | Body weight in kg |
| `time_of_day` | No | morning, evening, pre_workout, post_workout |
| `conditions` | No | fasted, post_meal, post_workout |

#### `measurement.logged`

```json
{
  "event_type": "measurement.logged",
  "data": {
    "type": "waist",
    "value_cm": 84
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | waist, chest, bicep, thigh, hip, neck, calf, forearm |
| `value_cm` | Yes | Measurement in centimeters |
| `side` | No | left, right (for bilateral measurements) |

### Event Conventions: recovery

#### `sleep.logged`

```json
{
  "event_type": "sleep.logged",
  "data": {
    "duration_hours": 7.5,
    "quality": "good"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `duration_hours` | Yes | Total sleep duration |
| `quality` | No | good, fair, poor |
| `bedtime` | No | HH:MM format |
| `wake_time` | No | HH:MM format |
| `notes` | No | Free text |

#### `soreness.logged`

```json
{
  "event_type": "soreness.logged",
  "data": {
    "area": "legs",
    "severity": 7
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `area` | Yes | legs, back, shoulders, chest, arms, full_body, etc. |
| `severity` | Yes | 1-10 scale (1 = minimal, 10 = severe) |
| `notes` | No | Free text |

#### `energy.logged`

```json
{
  "event_type": "energy.logged",
  "data": {
    "level": 7,
    "time_of_day": "morning"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `level` | Yes | 1-10 scale (1 = exhausted, 10 = peak) |
| `time_of_day` | No | morning, afternoon, evening, pre_workout, post_workout |
| `notes` | No | Free text |

### Event Conventions: nutrition

#### `meal.logged`

```json
{
  "event_type": "meal.logged",
  "data": {
    "calories": 650,
    "protein_g": 45,
    "carbs_g": 60,
    "fat_g": 25,
    "meal_type": "lunch",
    "description": "Chicken rice bowl with vegetables"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `calories` | No* | Total calories |
| `protein_g` | No | Protein in grams |
| `carbs_g` | No | Carbohydrates in grams |
| `fat_g` | No | Fat in grams |
| `meal_type` | No | breakfast, lunch, dinner, snack, pre_workout, post_workout |
| `description` | No | Free text description |

*At least calories or one macro should be provided. Handlers are tolerant.

### Event Conventions: training_plan

The only **prescriptive** event family. All other events describe what happened;
these describe what SHOULD happen.

Plans are weekly templates with named sessions. The agent derives concrete
loads from `exercise_progression` at conversation time — the plan says WHAT
and HOW MUCH, not HOW HEAVY.

#### `training_plan.created`

```json
{
  "event_type": "training_plan.created",
  "data": {
    "plan_id": "plan_531_bbb",
    "name": "5/3/1 Boring But Big",
    "sessions": [
      {
        "name": "Squat Day",
        "day_hint": "monday",
        "exercises": [
          {"exercise_id": "barbell_back_squat", "sets": 3, "rep_scheme": "5/3/1", "intensity": "program"},
          {"exercise_id": "barbell_back_squat", "sets": 5, "reps": 10, "intensity": "50%"},
          {"exercise_id": "leg_curl", "sets": 3, "reps": 12}
        ]
      },
      {
        "name": "Bench Day",
        "day_hint": "wednesday",
        "exercises": [
          {"exercise_id": "barbell_bench_press", "sets": 3, "rep_scheme": "5/3/1", "intensity": "program"},
          {"exercise_id": "barbell_bench_press", "sets": 5, "reps": 10, "intensity": "50%"},
          {"exercise_id": "dumbbell_row", "sets": 5, "reps": 10}
        ]
      }
    ],
    "cycle_weeks": 3,
    "notes": "Week 1: 5s, Week 2: 3s, Week 3: 5/3/1"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `plan_id` | No | Unique plan identifier (defaults to "default") |
| `name` | No | Human-readable plan name |
| `sessions` | Yes | List of session templates |
| `sessions[].name` | Yes | Session name ("Push Day", "Upper A") |
| `sessions[].day_hint` | No | Suggested weekday (advisory, not binding) |
| `sessions[].exercises` | Yes | List of prescribed exercises |
| `sessions[].exercises[].exercise_id` | Yes | Canonical exercise ID |
| `sessions[].exercises[].sets` | No | Number of sets |
| `sessions[].exercises[].reps` | No | Reps per set (fixed) |
| `sessions[].exercises[].rep_scheme` | No | Named scheme ("5/3/1", "5x5", "pyramid") |
| `sessions[].exercises[].intensity` | No | "program", "50%", "RPE 8", etc. |
| `cycle_weeks` | No | Weeks per cycle (for periodized programs) |
| `notes` | No | Free text notes about the program |

#### `training_plan.updated`

Delta merge on the plan identified by `plan_id`. Only provided fields
are updated; omitted fields remain unchanged.

```json
{
  "event_type": "training_plan.updated",
  "data": {
    "plan_id": "plan_531_bbb",
    "sessions": [...]
  }
}
```

#### `training_plan.archived`

```json
{
  "event_type": "training_plan.archived",
  "data": {
    "plan_id": "plan_531_bbb",
    "reason": "Switching to hypertrophy block"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `plan_id` | No | Plan to archive (defaults to "default") |
| `reason` | No | Why the plan was archived |

### Design Rules for New Dimensions

1. **Orthogonality test.** Does this dimension overlap with an existing one?
   If yes, extend the existing dimension. If no, create a new one.
2. **Granularity checklist.** Before building, decide which levels
   (set/session/day/week/all-time) using the checklist from Decision 7.
3. **Event-first.** Define event conventions before building the handler.
   The handler adapts to what events provide, not the other way around.
4. **Self-healing.** Full recompute on every event. No incremental state.
5. **Register in manifest.** Every dimension declares `dimension_meta` and
   `manifest_contribution` for the three-layer entry point.
