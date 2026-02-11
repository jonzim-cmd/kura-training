"""Semantic memory projection.

Builds per-user semantic suggestions from observed exercise/food terms.
Uses global catalog embeddings + user term embeddings with deterministic
fallback when external embedding models are unavailable.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..embeddings import cosine_similarity, get_embedding_provider
from ..registry import projection_handler
from ..utils import get_retracted_event_ids

logger = logging.getLogger(__name__)


def _normalize_term(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


async def _has_vector_column(conn: psycopg.AsyncConnection[Any], table: str) -> bool:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = %s
              AND column_name = 'embedding_vec'
            LIMIT 1
            """,
            (table,),
        )
        return await cur.fetchone() is not None


def _parse_embedding(value: Any) -> list[float]:
    if isinstance(value, list):
        return [float(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [float(v) for v in parsed]
        except (ValueError, TypeError):
            return []
    return []


async def _load_catalog_embeddings(
    conn: psycopg.AsyncConnection[Any],
    domain: str,
    model: str,
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT c.canonical_key, c.canonical_label, ce.embedding
            FROM semantic_catalog c
            JOIN semantic_catalog_embeddings ce ON ce.catalog_id = c.id
            WHERE c.domain = %s
              AND ce.model = %s
            """,
            (domain, model),
        )
        rows = await cur.fetchall()
    out = []
    for row in rows:
        vec = _parse_embedding(row["embedding"])
        if not vec:
            continue
        out.append(
            {
                "canonical_key": row["canonical_key"],
                "canonical_label": row["canonical_label"],
                "embedding": vec,
            }
        )
    return out


async def _load_user_embeddings(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    domain: str,
    model: str,
) -> dict[str, list[float]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT term_text, embedding
            FROM semantic_user_embeddings
            WHERE user_id = %s
              AND domain = %s
              AND model = %s
            """,
            (user_id, domain, model),
        )
        rows = await cur.fetchall()
    out: dict[str, list[float]] = {}
    for row in rows:
        vec = _parse_embedding(row["embedding"])
        if vec:
            out[row["term_text"]] = vec
    return out


async def _upsert_user_embedding(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    domain: str,
    term_text: str,
    canonical_key: str | None,
    provider: str,
    model: str,
    dimensions: int,
    embedding: list[float],
    write_vector_column: bool,
) -> None:
    if write_vector_column and len(embedding) == 384:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO semantic_user_embeddings (
                    user_id, domain, term_text, canonical_key,
                    provider, model, dimensions, embedding, embedding_vec
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (user_id, domain, term_text, model) DO UPDATE SET
                    canonical_key = EXCLUDED.canonical_key,
                    provider = EXCLUDED.provider,
                    dimensions = EXCLUDED.dimensions,
                    embedding = EXCLUDED.embedding,
                    embedding_vec = EXCLUDED.embedding_vec,
                    updated_at = NOW()
                """,
                (
                    user_id,
                    domain,
                    term_text,
                    canonical_key,
                    provider,
                    model,
                    dimensions,
                    json.dumps(embedding),
                    _vector_literal(embedding),
                ),
            )
        return

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO semantic_user_embeddings (
                user_id, domain, term_text, canonical_key,
                provider, model, dimensions, embedding
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, domain, term_text, model) DO UPDATE SET
                canonical_key = EXCLUDED.canonical_key,
                provider = EXCLUDED.provider,
                dimensions = EXCLUDED.dimensions,
                embedding = EXCLUDED.embedding,
                updated_at = NOW()
            """,
            (
                user_id,
                domain,
                term_text,
                canonical_key,
                provider,
                model,
                dimensions,
                json.dumps(embedding),
            ),
        )


def _best_match(
    term_vec: list[float],
    catalog_embeddings: list[dict[str, Any]],
) -> tuple[str, str, float] | None:
    best: tuple[str, str, float] | None = None
    for item in catalog_embeddings:
        score = cosine_similarity(term_vec, item["embedding"])
        if best is None or score > best[2]:
            best = (item["canonical_key"], item["canonical_label"], score)
    return best


def _manifest_contribution(projection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not projection_rows:
        return {}
    data = projection_rows[0]["data"]
    return {
        "exercise_candidates": len(data.get("exercise_candidates", [])),
        "food_candidates": len(data.get("food_candidates", [])),
        "indexed_terms": data.get("indexed_terms", {}),
    }


@projection_handler("set.logged", "exercise.alias_created", "meal.logged", dimension_meta={
    "name": "semantic_memory",
    "description": "Semantic resolution candidates from user-specific language and global catalog",
    "key_structure": "single overview per user",
    "projection_key": "overview",
    "granularity": ["all_time"],
    "relates_to": {
        "user_profile": {"join": "data_quality.actionable", "why": "resolve unresolved terms"},
        "exercise_progression": {"join": "exercise_id", "why": "canonical mapping for progression"},
        "nutrition": {"join": "food", "why": "canonical food mapping"},
    },
    "context_seeds": [
        "exercise_vocabulary",
        "nutrition_interest",
        "language",
    ],
    "output_schema": {
        "indexed_terms": {"exercise": "integer", "food": "integer"},
        "exercise_candidates": [{
            "term": "string",
            "count": "integer",
            "suggested_exercise_id": "string",
            "label": "string",
            "score": "number",
            "confidence": "string — high|medium|low",
        }],
        "food_candidates": [{
            "term": "string",
            "count": "integer",
            "suggested_food_id": "string",
            "label": "string",
            "score": "number",
            "confidence": "string — high|medium|low",
        }],
        "provider": {
            "provider": "string",
            "model": "string",
            "dimensions": "integer",
        },
        "data_quality": {
            "min_similarity_threshold": "number",
            "unresolved_exercise_terms": "integer",
            "total_terms_observed": "integer",
        },
    },
    "manifest_contribution": _manifest_contribution,
})
async def update_semantic_memory(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id = payload["user_id"]
    retracted_ids = await get_retracted_event_ids(conn, user_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, event_type, data
            FROM events
            WHERE user_id = %s
              AND event_type IN ('set.logged', 'exercise.alias_created', 'meal.logged')
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    if not rows:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM projections
                WHERE user_id = %s
                  AND projection_type = 'semantic_memory'
                  AND key = 'overview'
                """,
                (user_id,),
            )
        return

    exercise_terms: Counter[str] = Counter()
    food_terms: Counter[str] = Counter()
    unresolved_exercise_terms: set[str] = set()

    for row in rows:
        event_type = row["event_type"]
        data = row["data"] or {}

        if event_type == "set.logged":
            ex = _normalize_term(data.get("exercise"))
            if ex:
                exercise_terms[ex] += 1
                if not _normalize_term(data.get("exercise_id")):
                    unresolved_exercise_terms.add(ex)
        elif event_type == "exercise.alias_created":
            alias = _normalize_term(data.get("alias"))
            if alias:
                exercise_terms[alias] += 1
        elif event_type == "meal.logged":
            food = _normalize_term(data.get("food") or data.get("name"))
            if food:
                food_terms[food] += 1
            items = data.get("items")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = _normalize_term(str(item.get("name", "")))
                    if name:
                        food_terms[name] += 1

    provider = get_embedding_provider()
    provider_info = provider.descriptor()
    model = str(provider_info["model"])
    provider_name = str(provider_info["provider"])
    dimensions = int(provider_info["dimensions"])

    user_has_vec = await _has_vector_column(conn, "semantic_user_embeddings")

    # Load existing term embeddings and materialize missing ones.
    exercise_emb = await _load_user_embeddings(conn, user_id, "exercise", model)
    food_emb = await _load_user_embeddings(conn, user_id, "food", model)

    missing_ex_terms = [t for t in exercise_terms if t not in exercise_emb]
    missing_food_terms = [t for t in food_terms if t not in food_emb]

    if missing_ex_terms:
        vectors = provider.embed_many(missing_ex_terms)
        for term, vec in zip(missing_ex_terms, vectors):
            await _upsert_user_embedding(
                conn,
                user_id=user_id,
                domain="exercise",
                term_text=term,
                canonical_key=None,
                provider=provider_name,
                model=model,
                dimensions=dimensions,
                embedding=vec,
                write_vector_column=user_has_vec,
            )
            exercise_emb[term] = vec

    if missing_food_terms:
        vectors = provider.embed_many(missing_food_terms)
        for term, vec in zip(missing_food_terms, vectors):
            await _upsert_user_embedding(
                conn,
                user_id=user_id,
                domain="food",
                term_text=term,
                canonical_key=None,
                provider=provider_name,
                model=model,
                dimensions=dimensions,
                embedding=vec,
                write_vector_column=user_has_vec,
            )
            food_emb[term] = vec

    exercise_catalog = await _load_catalog_embeddings(conn, "exercise", model)
    food_catalog = await _load_catalog_embeddings(conn, "food", model)

    min_score = float(os.environ.get("KURA_SEMANTIC_MIN_SCORE", "0.72"))

    exercise_candidates: list[dict[str, Any]] = []
    for term, count in exercise_terms.most_common(100):
        vec = exercise_emb.get(term)
        if not vec:
            continue
        best = _best_match(vec, exercise_catalog)
        if not best:
            continue
        key, label, score = best
        if score < min_score:
            continue
        confidence = "high" if score >= 0.86 else ("medium" if score >= 0.78 else "low")
        exercise_candidates.append(
            {
                "term": term,
                "count": count,
                "suggested_exercise_id": key,
                "label": label,
                "score": round(score, 4),
                "confidence": confidence,
            }
        )

    food_candidates: list[dict[str, Any]] = []
    for term, count in food_terms.most_common(100):
        vec = food_emb.get(term)
        if not vec:
            continue
        best = _best_match(vec, food_catalog)
        if not best:
            continue
        key, label, score = best
        if score < min_score:
            continue
        confidence = "high" if score >= 0.86 else ("medium" if score >= 0.78 else "low")
        food_candidates.append(
            {
                "term": term,
                "count": count,
                "suggested_food_id": key,
                "label": label,
                "score": round(score, 4),
                "confidence": confidence,
            }
        )

    projection_data = {
        "indexed_terms": {
            "exercise": len(exercise_terms),
            "food": len(food_terms),
        },
        "exercise_candidates": exercise_candidates[:50],
        "food_candidates": food_candidates[:50],
        "provider": {
            "provider": provider_name,
            "model": model,
            "dimensions": dimensions,
        },
        "data_quality": {
            "min_similarity_threshold": min_score,
            "unresolved_exercise_terms": len(unresolved_exercise_terms),
            "total_terms_observed": len(exercise_terms) + len(food_terms),
        },
    }

    last_event_id = str(rows[-1]["id"])
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, 'semantic_memory', 'overview', %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (user_id, json.dumps(projection_data), last_event_id),
        )

    logger.info(
        "Updated semantic_memory for user=%s (exercise_terms=%d, food_terms=%d)",
        user_id,
        len(exercise_terms),
        len(food_terms),
    )
