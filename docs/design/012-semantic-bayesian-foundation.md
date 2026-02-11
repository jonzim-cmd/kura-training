# Decision 12: Semantic Layer + Bayesian Inference Foundation

Status: **Accepted** (2026-02-11)

## Goal

Introduce a production-grade foundation for:

1. **Semantic resolution** (exercise/food term understanding across languages)
2. **Bayesian inference projections** (strength trend + readiness uncertainty)

without training custom LLMs.

## Decisions

### D12.1 Embeddings Strategy: Local-First, Deterministic Fallback

- Primary provider: local `sentence-transformers` (multilingual)
- Optional provider: OpenAI embeddings
- Guaranteed fallback: deterministic hashing embeddings

This preserves:
- scalability and cost control (local-first),
- operational resilience (fallback always works),
- deterministic behavior in constrained environments.

### D12.2 Semantic Data Model

New persistent artifacts:

- `semantic_catalog`: global canonical concepts (exercise/food)
- `semantic_variants`: lexical variants and multilingual labels
- `semantic_catalog_embeddings`: global concept embeddings
- `semantic_user_embeddings`: user-observed term embeddings

Canonical embedding storage is JSONB. Optional `pgvector` columns are enabled
when extension `vector` is available.

### D12.3 Semantic Projection

New projection: `semantic_memory/overview`

Contains:
- indexed user terms (exercise + food)
- semantic candidate mappings with confidence bands
- embedding provider/model metadata

Purpose:
- Assist agent normalization (`exercise`/`food` -> canonical id)
- Reduce repetitive clarification loops
- Keep all behavior inspectable and reversible

### D12.6 Semantic Resolve API Contract

Endpoint: `POST /v1/semantic/resolve`

Purpose:
- Let agents submit free-text terms for `exercise`/`food`
- Return ranked canonical candidates with confidence bands and provenance
- Reuse semantic foundation artifacts instead of embedding logic in each agent

Request (batch-friendly):

```json
{
  "queries": [
    { "term": "Kniebeuge", "domain": "exercise" },
    { "term": "Haferflocken", "domain": "food" }
  ],
  "top_k": 5
}
```

Resolution strategy (in order):
1. Exact term hits from `semantic_memory/overview` candidates
2. Exact catalog/variant matches from `semantic_catalog` + `semantic_variants`
3. Embedding similarity via `semantic_user_embeddings` + `semantic_catalog_embeddings`
   - If provider is `hashing` and no stored user embedding exists, runtime hashing fallback is used

Response characteristics:
- Per-query ranked candidates (`score`, `confidence`)
- Candidate `provenance` entries indicate source (`semantic_memory_projection`, `catalog_exact_match`, embedding similarity sources)
- Response `meta` includes provider/model and similarity threshold used during ranking

### D12.4 Bayesian Inference Projections

New projection: `strength_inference/<exercise_id>`
- Per-exercise probabilistic trend and near-term forecast
- Plateau/improvement probabilities
- Supports PyMC path with safe closed-form fallback
- Derivative-enriched `dynamics` payload (`velocity`, `acceleration`, discrete `trajectory_code`)
- Weekly cycle `phase` snapshot for low-resolution cyclic context

New projection: `readiness_inference/overview`
- Daily readiness posterior from sleep/energy/soreness/load signals
- Confidence intervals and state classification
- Derivative-enriched readiness dynamics and weekly cycle phase snapshot

### D12.5 Recompute Model

- Event-driven updates for freshness on relevant events
- Designed for scheduled nightly refit extension (same projection contract)

### D12.7 Durable Nightly Scheduler

Nightly refit orchestration uses a dedicated durable scheduler state
(`inference_scheduler_state`) instead of self-rescheduling jobs.

Properties:
- single-flight: at most one in-flight `inference.nightly_refit` job
- dedup: nightly `projection.update` jobs are de-duplicated while pending/processing
- recovery: failed/dead in-flight runs are detected and re-scheduled immediately
- telemetry: explicit `next_run_at`, `last_missed_runs`, catch-up counters, and run status

Catch-up behavior:
- If worker downtime causes missed slots, scheduler computes due run count and
  records missed-run telemetry, while scheduling one catch-up execution cycle.

### D12.8 Agent Context Ranking Layer

`GET /v1/agent/context` applies a projection ranking layer for list-like
context blocks (`exercise_progression`, `strength_inference`, `custom`).

Ranking dimensions:
- recency (projection update age)
- confidence (projection-specific reliability heuristics)
- semantic relevance (task intent token overlap + semantic memory candidate hints)
- task intent alignment (projection-type affinity)

This is retrieval orchestration only. It intentionally stays separate from
the semantic resolve endpoint contract and does not change canonical
resolution behavior.

## Design Principles

1. **No black boxes**: projections include diagnostics + uncertainty metadata
2. **Graceful degradation**: semantic and Bayesian pipelines have explicit fallback
3. **Agent-first contracts**: outputs are structured for direct agent action
4. **No custom LLM training**: leverage pretrained embeddings + probabilistic models

## Open Follow-ups

- Scheduled nightly refit orchestration (job-level lifecycle)
- Calibration/eval harness and shadow-mode rollout checks
- Population priors (privacy-gated, opt-in) and causal layer
