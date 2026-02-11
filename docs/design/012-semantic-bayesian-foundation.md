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

### D12.4 Bayesian Inference Projections

New projection: `strength_inference/<exercise_id>`
- Per-exercise probabilistic trend and near-term forecast
- Plateau/improvement probabilities
- Supports PyMC path with safe closed-form fallback

New projection: `readiness_inference/overview`
- Daily readiness posterior from sleep/energy/soreness/load signals
- Confidence intervals and state classification

### D12.5 Recompute Model

- Event-driven updates for freshness on relevant events
- Designed for scheduled nightly refit extension (same projection contract)

## Design Principles

1. **No black boxes**: projections include diagnostics + uncertainty metadata
2. **Graceful degradation**: semantic and Bayesian pipelines have explicit fallback
3. **Agent-first contracts**: outputs are structured for direct agent action
4. **No custom LLM training**: leverage pretrained embeddings + probabilistic models

## Open Follow-ups

- Scheduled nightly refit orchestration (job-level lifecycle)
- Calibration/eval harness and shadow-mode rollout checks
- Population priors (privacy-gated, opt-in) and causal layer
