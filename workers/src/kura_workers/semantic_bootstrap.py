"""Semantic catalog bootstrap and embedding materialization."""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .embeddings import get_embedding_provider
from .semantic_catalog import all_catalog_entries

logger = logging.getLogger(__name__)


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


async def _catalog_embedding_has_vector_column(conn: psycopg.AsyncConnection[Any]) -> bool:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'semantic_catalog_embeddings'
              AND column_name = 'embedding_vec'
            LIMIT 1
            """
        )
        return await cur.fetchone() is not None


async def ensure_semantic_catalog(conn: psycopg.AsyncConnection[Any]) -> None:
    """Upsert static catalog entries + materialize global embeddings."""
    provider = get_embedding_provider()
    provider_info = provider.descriptor()

    has_vector_column = await _catalog_embedding_has_vector_column(conn)

    entries = all_catalog_entries()
    texts_to_embed: list[str] = []
    catalog_ids: list[str] = []

    for entry in entries:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO semantic_catalog (domain, canonical_key, canonical_label, metadata)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (domain, canonical_key) DO UPDATE SET
                    canonical_label = EXCLUDED.canonical_label,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    entry.domain,
                    entry.canonical_key,
                    entry.canonical_label,
                    json.dumps(entry.metadata),
                ),
            )
            row = await cur.fetchone()
            if row is None:
                continue
            catalog_id = str(row["id"])

        for variant in {entry.canonical_label, *entry.variants}:
            norm_variant = variant.strip().lower()
            if not norm_variant:
                continue
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO semantic_variants (catalog_id, variant_text, source)
                    VALUES (%s, %s, 'seed')
                    ON CONFLICT (catalog_id, lower(variant_text)) DO NOTHING
                    """,
                    (catalog_id, norm_variant),
                )

        # Embedding text intentionally mixes canonical label + known variants.
        embed_text = ", ".join((entry.canonical_label, *entry.variants))
        texts_to_embed.append(embed_text)
        catalog_ids.append(catalog_id)

    vectors = provider.embed_many(texts_to_embed)
    model = str(provider_info["model"])
    provider_name = str(provider_info["provider"])
    dimensions = int(provider_info["dimensions"])

    for catalog_id, vec in zip(catalog_ids, vectors):
        if len(vec) != dimensions:
            # Dimension mismatch should never happen, but keep writes safe.
            logger.warning(
                "Skipping catalog embedding due to dim mismatch (catalog_id=%s, got=%d, expected=%d)",
                catalog_id,
                len(vec),
                dimensions,
            )
            continue

        if has_vector_column and len(vec) == 384:
            vec_literal = _vector_literal(vec)
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO semantic_catalog_embeddings (
                        catalog_id, provider, model, dimensions, embedding, embedding_vec
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (catalog_id, model) DO UPDATE SET
                        provider = EXCLUDED.provider,
                        dimensions = EXCLUDED.dimensions,
                        embedding = EXCLUDED.embedding,
                        embedding_vec = EXCLUDED.embedding_vec,
                        updated_at = NOW()
                    """,
                    (
                        catalog_id,
                        provider_name,
                        model,
                        dimensions,
                        json.dumps(vec),
                        vec_literal,
                    ),
                )
        else:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO semantic_catalog_embeddings (
                        catalog_id, provider, model, dimensions, embedding
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (catalog_id, model) DO UPDATE SET
                        provider = EXCLUDED.provider,
                        dimensions = EXCLUDED.dimensions,
                        embedding = EXCLUDED.embedding,
                        updated_at = NOW()
                    """,
                    (
                        catalog_id,
                        provider_name,
                        model,
                        dimensions,
                        json.dumps(vec),
                    ),
                )

    logger.info(
        "Semantic catalog ensured (entries=%d, provider=%s, model=%s, dims=%d)",
        len(entries),
        provider_name,
        model,
        dimensions,
    )
