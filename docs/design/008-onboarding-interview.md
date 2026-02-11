# Design 008: Onboarding Interview

Status: **Decided** (2026-02-08)

## Context

Kura has a working Event Store, Auth, Projections, and a three-layer agent entry
point (Decision 7). The next question: how does a new user's profile get
bootstrapped? The agent needs enough context to work effectively from the first
interaction.

Inspired by [Anthropic Interviewer](https://www.anthropic.com/research/anthropic-interviewer)
— a trained LLM that conducts adaptive interviews, reacting to context rather
than following a script. We can't train a custom model, but we don't need to.
A general LLM with the right context achieves the same result.

## Core Principle: Information Landscape, Not Questions

Instead of pre-formulating questions (which impose a rigid structure), we
describe the **information landscape** — what the system can analyze, what
context makes each dimension more valuable, and what events to produce. Any
general LLM (Claude, GPT, custom) reads this landscape and conducts a natural,
adaptive conversation.

The interview is **advisory, not modal**. The user can:
- Refuse the interview entirely
- Redirect to a different topic mid-interview
- Come back to it later
- Never complete it — partial data is still useful

## Decision 8.1: Interview Guide in System Layer

The `system` layer of `user_profile` gains an `interview_guide` section:

```json
"system": {
  "dimensions": { ... },
  "time_conventions": { ... },
  "interview_guide": {
    "philosophy": ["..."],
    "phases": { ... },
    "coverage_areas": [ ... ],
    "event_conventions": { ... }
  }
}
```

Static content, identical for all users. Describes HOW to interview, not WHAT
to ask. The agent uses this alongside the `user` layer (what's known) and
`agenda` layer (what to do) to conduct the conversation.

### Philosophy

The guide encodes interviewing principles, not questions:

- Follow the conversation, don't interrogate
- Extract multiple data points from one answer
- Use structured options for factual info, open questions for narrative
- Produce events incrementally during the conversation
- Respect "later" — mark as deferred, move on
- Show the user what Kura can do with the information

### Phases

| Phase | Goal | Duration | Rules |
|-------|------|----------|-------|
| Broad Sweep | Cover all areas at surface level | ~5-10 exchanges | 1-2 exchanges per area. Categorical where possible. Move on after 3 max. |
| Targeted Depth | Go deeper where gaps or interest exist | ~3-5 exchanges | Use dimension coverage + context_seeds. Focus on high-value areas. |
| Wrap Up | Summarize, confirm, show what Kura can do | ~1-2 exchanges | Review what was learned. Highlight next steps. |

Boundaries are advisory. The agent adapts to the user's pace and interest.

### Coverage Areas

Each area has an `approach` that guides the agent's questioning style:

| Area | Approach | Produces |
|------|----------|----------|
| Training background | categorical | `profile.updated` |
| Baseline profile completeness | categorical → narrative | `profile.updated`, `bodyweight.logged` |
| Goals | narrative | `goal.set` |
| Exercise vocabulary | conversational | `exercise.alias_created` |
| Unit preferences | categorical | `preference.set` |
| Injuries/limitations | categorical → narrative | `injury.reported` |
| Equipment | categorical | `profile.updated` |
| Schedule/frequency | categorical | `profile.updated` |
| Nutrition interest | categorical | `preference.set` |
| Current program | narrative | `profile.updated`, `program.started` |

**Approach types:**
- `categorical`: Structured options, quick answers (e.g., "Kraft / Ausdauer / Hybrid?")
- `narrative`: Open-ended, follow the thread (e.g., "Was willst du erreichen?")
- `conversational`: Emerge from natural dialogue (e.g., exercises mentioned in passing)
- `categorical_then_narrative`: Start with yes/no, go deeper if yes

## Decision 8.2: `context_seeds` in Dimension Metadata

Each dimension declares what information makes it more valuable — not as
questions, but as information axes the agent should explore:

```python
@projection_handler("set.logged", dimension_meta={
    "name": "exercise_progression",
    "context_seeds": [
        "exercise_vocabulary",
        "training_modality",
        "experience_level",
        "typical_rep_ranges",
    ],
    ...
})
```

The agent reads `context_seeds` and naturally weaves them into the conversation.
"Training modality" isn't a question — it's a dimension the agent explores.
If the user says "Ich mache Powerlifting seit 5 Jahren", that covers modality
AND experience AND likely vocabulary and rep ranges.

## Decision 8.3: `profile.updated` Event Type

For user attributes that don't fit existing event types:

```json
{
  "event_type": "profile.updated",
  "data": {
    "experience_level": "intermediate",
    "training_modality": "strength",
    "training_frequency_per_week": 4,
    "available_equipment": ["barbell", "dumbbells", "rack"],
    "primary_location": "home_gym"
  }
}
```

Free-form JSONB. No schema validation. Handlers are tolerant of any subset of
fields. Each `profile.updated` event is a delta — the projection merges them
chronologically (last event per field wins).

### Supported fields (convention, not schema)

| Field | Type | Example |
|-------|------|---------|
| `experience_level` | string | `"beginner"`, `"intermediate"`, `"advanced"` |
| `training_modality` | string | `"strength"`, `"endurance"`, `"hybrid"`, `"crossfit"` |
| `training_frequency_per_week` | number | `4` |
| `available_equipment` | list[string] | `["barbell", "dumbbells"]` |
| `primary_location` | string | `"commercial_gym"`, `"home_gym"`, `"outdoor"` |
| `current_program` | string | `"5/3/1"`, `"PPL"`, `"custom"` |
| `nutrition_tracking` | string | `"active"`, `"not_interested"`, `"later"` |
| `age` | number | `34` |
| `date_of_birth` | string (ISO date) | `"1992-04-17"` |
| `age_deferred` / `date_of_birth_deferred` | boolean | `true` |
| `bodyweight_kg` | number | `82.4` |
| `bodyweight_deferred` | boolean | `true` |
| `sex` / `sex_deferred` | string / boolean | `"female"` / `true` |
| `body_fat_pct` / `body_fat_pct_deferred` | number / boolean | `18.5` / `true` |
| `body_composition_deferred` | boolean | `true` |

## Decision 8.4: Interview Coverage Computation

Pure-function logic determines which coverage areas are filled:

| Area | Covered when |
|------|-------------|
| Training background | `profile.updated` with `training_modality` or `experience_level` |
| Baseline profile completeness | Required slots (`age` or `date_of_birth`, plus bodyweight via `profile.updated.bodyweight_kg` or `bodyweight.logged`) are known or explicitly deferred |
| Goals | Any `goal.set` event |
| Exercise vocabulary | 3+ `exercise.alias_created` events |
| Unit preferences | `preference.set` with key `unit_system` |
| Injuries | Any `injury.reported` event, or `profile.updated` with `injuries_none: true` |
| Equipment | `profile.updated` with `available_equipment` |
| Schedule | `profile.updated` with `training_frequency_per_week` |
| Nutrition interest | `preference.set` with key `nutrition_tracking` |
| Current program | `profile.updated` with `current_program`, or `program.started` event |

Coverage status values: `covered`, `uncovered`, `needs_depth`, `deferred`.

The coverage map is included in the `user` layer alongside `data_quality`:

```json
"user": {
  "interview_coverage": [
    {"area": "training_background", "status": "covered"},
    {"area": "goals", "status": "uncovered"},
    {"area": "exercise_vocabulary", "status": "needs_depth", "note": "2 aliases, suggest more"}
  ]
}
```

The agent can re-read `user_profile` during the interview to check progress.

## Decision 8.5: Agenda Triggers

### `onboarding_needed` (HIGH priority)

Triggers when **all** of these are true:
- Total events < 5
- Most coverage areas are `uncovered`

```json
{
  "priority": "high",
  "type": "onboarding_needed",
  "detail": "New user with minimal data. Interview recommended to bootstrap profile.",
  "dimensions": ["user_profile"]
}
```

### `profile_refresh_suggested` (MEDIUM priority)

Triggers when:
- Total events > 20 (not a new user)
- 3+ coverage areas are `uncovered`
- No `goal.set` events, or no `preference.set` events

```json
{
  "priority": "medium",
  "type": "profile_refresh_suggested",
  "detail": "User has training data but missing context (goals, preferences). Brief interview would improve analysis.",
  "dimensions": ["user_profile"]
}
```

## Decision 8.6: Empty User Bootstrap

### Problem

For brand-new users with zero events, the Python worker never fires. No events
→ no trigger → no worker → no `user_profile` projection → agent gets 404.

### Solution

The Rust projection endpoint returns a bootstrap response for `user_profile/me`
when no projection exists (HTTP 200, version 0):

```json
{
  "projection_type": "user_profile",
  "key": "me",
  "data": {
    "system": null,
    "user": null,
    "agenda": [
      {
        "priority": "high",
        "type": "onboarding_needed",
        "detail": "New user. No data yet. Produce initial events to bootstrap profile."
      }
    ]
  },
  "version": 0
}
```

The agent sees `system: null` → knows capabilities aren't loaded yet.
The agent sees `agenda: [onboarding_needed]` → knows to start interviewing.

After the first event (e.g., `preference.set` for language), the worker fires,
builds the full three-layer response, and subsequent reads return the complete
`user_profile` with interview guide.

### Agent Workflow (New User)

```
1. GET /v1/projections/user_profile/me → bootstrap (system: null)
2. Agent produces first event (e.g., preference.set language)
3. Worker fires → full user_profile built
4. GET /v1/projections/user_profile/me → full three-layer with interview_guide
5. Agent continues interview using the guide
```

## Accepted Trade-offs

### Interview guide is static, not per-user customizable
Same guide for all users. Accepted because: simplicity, consistency, fast
iteration. The guide describes the landscape — the agent personalizes the
conversation. Revisit if per-tenant customization is needed.

### No forced interview flow
Guidance only. User can refuse, redirect, or abandon. Accepted because: agent
freedom, user autonomy, simpler implementation. Partial data is still valuable.

### Two reads for new users
Bootstrap response lacks the full system layer. Agent needs a second read after
the first event. Accepted because: only happens once, and the alternative
(duplicating Python dimension metadata in Rust) creates maintenance burden.

### Coverage computation is heuristic
Simple event counting, not semantic understanding. "3+ aliases = vocabulary
covered" is approximate. Accepted because: good enough for triggering, and
the agent can judge for itself whether to continue.

## Implementation Priority

1. Interview guide module + `context_seeds` on handlers
2. `profile.updated` event type + coverage computation in user_profile handler
3. Rust bootstrap response for empty users
4. Agenda triggers (`onboarding_needed`, `profile_refresh_suggested`)
5. Testing (unit + manual interview test)
