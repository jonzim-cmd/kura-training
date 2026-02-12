"""Async external import job handler (tm5.6)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..external_identity import ExistingImportRecord
from ..external_import_pipeline import ImportPipelineError, build_import_plan
from ..registry import register

logger = logging.getLogger(__name__)


def _event_uuid() -> str:
    try:
        return str(uuid.uuid7())
    except AttributeError:
        return str(uuid.uuid4())


async def _fetch_import_job(
    conn: psycopg.AsyncConnection[Any],
    *,
    import_job_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT *
            FROM external_import_jobs
            WHERE id = %s
              AND user_id = %s
            """,
            (import_job_id, user_id),
        )
        return await cur.fetchone()


async def _update_import_job_status(
    conn: psycopg.AsyncConnection[Any],
    *,
    import_job_id: str,
    status: str,
    receipt: dict[str, Any],
    error_code: str | None = None,
    error_message: str | None = None,
    source_identity_key: str | None = None,
    payload_fingerprint: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    completed_at = "NOW()" if status in {"completed", "failed"} else "NULL"
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            UPDATE external_import_jobs
            SET status = %s,
                receipt = %s,
                error_code = %s,
                error_message = %s,
                source_identity_key = COALESCE(%s, source_identity_key),
                payload_fingerprint = COALESCE(%s, payload_fingerprint),
                idempotency_key = COALESCE(%s, idempotency_key),
                completed_at = {completed_at}
            WHERE id = %s
            """,
            (
                status,
                Json(receipt),
                error_code,
                error_message,
                source_identity_key,
                payload_fingerprint,
                idempotency_key,
                import_job_id,
            ),
        )


async def _mark_processing(
    conn: psycopg.AsyncConnection[Any],
    *,
    import_job_id: str,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE external_import_jobs
            SET status = 'processing',
                started_at = COALESCE(started_at, NOW())
            WHERE id = %s
            """,
            (import_job_id,),
        )


async def _load_existing_records(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    provider: str,
    provider_user_id: str,
    external_activity_id: str,
    current_import_job_id: str,
) -> list[ExistingImportRecord]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT external_event_version, payload_fingerprint
            FROM external_import_jobs
            WHERE user_id = %s
              AND provider = %s
              AND provider_user_id = %s
              AND external_activity_id = %s
              AND status = 'completed'
              AND id <> %s
              AND payload_fingerprint IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 128
            """,
            (
                user_id,
                provider,
                provider_user_id,
                external_activity_id,
                current_import_job_id,
            ),
        )
        rows = await cur.fetchall()

    return [
        ExistingImportRecord(
            external_event_version=(
                str(row["external_event_version"])
                if row.get("external_event_version") is not None
                else None
            ),
            payload_fingerprint=str(row["payload_fingerprint"]),
        )
        for row in rows
    ]


async def _find_existing_event_id(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    idempotency_key: str,
) -> str | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id
            FROM events
            WHERE user_id = %s
              AND metadata->>'idempotency_key' = %s
            LIMIT 1
            """,
            (user_id, idempotency_key),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return str(row["id"])


async def _insert_external_activity_event(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    import_job_id: str,
    provider: str,
    idempotency_key: str,
    canonical_payload: dict[str, Any],
) -> str:
    event_id = _event_uuid()
    event_timestamp = canonical_payload.get("session", {}).get("started_at")
    if not event_timestamp:
        raise ValueError("canonical payload missing session.started_at")

    metadata = {
        "source": "import",
        "agent": f"external_import:{provider}",
        "session_id": f"import:{import_job_id}",
        "idempotency_key": idempotency_key,
    }
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO events (id, user_id, timestamp, event_type, data, metadata)
            VALUES (%s, %s, %s, 'external.activity_imported', %s, %s)
            """,
            (
                event_id,
                user_id,
                event_timestamp,
                Json(canonical_payload),
                Json(metadata),
            ),
        )
    return event_id


async def _enqueue_import_quality_refresh(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    import_job_id: str,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO background_jobs (user_id, job_type, payload, max_retries)
            VALUES (%s, 'projection.update', %s, 3)
            """,
            (
                user_id,
                Json(
                    {
                        "event_id": import_job_id,
                        "event_type": "external.import.job",
                        "user_id": user_id,
                    }
                ),
            ),
        )


@register("external_import.process")
async def handle_external_import_process(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    import_job_id = str(payload.get("import_job_id") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    if not import_job_id or not user_id:
        raise ValueError("external_import.process payload requires import_job_id + user_id")

    job = await _fetch_import_job(conn, import_job_id=import_job_id, user_id=user_id)
    if job is None:
        raise ValueError(f"external_import job not found: {import_job_id}")

    await _mark_processing(conn, import_job_id=import_job_id)

    provider = str(job["provider"])
    provider_user_id = str(job["provider_user_id"])
    file_format = str(job["file_format"])
    external_activity_id = str(job["external_activity_id"])
    external_event_version = (
        str(job["external_event_version"])
        if job.get("external_event_version") is not None
        else None
    )
    ingestion_method = str(job.get("ingestion_method") or "file_import")
    payload_text = str(job["payload_text"])

    existing_records = await _load_existing_records(
        conn,
        user_id=user_id,
        provider=provider,
        provider_user_id=provider_user_id,
        external_activity_id=external_activity_id,
        current_import_job_id=import_job_id,
    )

    try:
        plan = build_import_plan(
            provider=provider,
            provider_user_id=provider_user_id,
            external_activity_id=external_activity_id,
            file_format=file_format,  # type: ignore[arg-type]
            payload_text=payload_text,
            external_event_version=external_event_version,
            existing_records=existing_records,
            ingestion_method=ingestion_method,  # type: ignore[arg-type]
        )
    except ImportPipelineError as exc:
        receipt = {
            "status": "failed",
            "error_code": exc.code,
            "error_field": exc.field,
            "docs_hint": exc.docs_hint,
            "message": str(exc),
        }
        await _update_import_job_status(
            conn,
            import_job_id=import_job_id,
            status="failed",
            receipt=receipt,
            error_code=exc.code,
            error_message=str(exc),
        )
        await _enqueue_import_quality_refresh(
            conn,
            user_id=user_id,
            import_job_id=import_job_id,
        )
        return

    dedup = {
        "decision": plan.dedup_result.decision,
        "outcome": plan.dedup_result.outcome,
        "reason": plan.dedup_result.reason,
    }
    base_receipt = {
        "status": "completed",
        "format": file_format,
        "provider": provider,
        "dedup": dedup,
        "mapping_version": plan.mapping_version,
        "unsupported_fields": plan.unsupported_fields,
        "warnings": plan.warnings,
    }

    if plan.dedup_result.decision == "reject":
        await _update_import_job_status(
            conn,
            import_job_id=import_job_id,
            status="failed",
            receipt={**base_receipt, "status": "failed"},
            error_code=plan.dedup_result.outcome,
            error_message=plan.dedup_result.reason,
            source_identity_key=plan.source_identity_key,
            payload_fingerprint=plan.payload_fingerprint,
            idempotency_key=plan.idempotency_key,
        )
        await _enqueue_import_quality_refresh(
            conn,
            user_id=user_id,
            import_job_id=import_job_id,
        )
        return

    if not plan.should_write:
        await _update_import_job_status(
            conn,
            import_job_id=import_job_id,
            status="completed",
            receipt={**base_receipt, "write": {"result": "duplicate_skipped"}},
            source_identity_key=plan.source_identity_key,
            payload_fingerprint=plan.payload_fingerprint,
            idempotency_key=plan.idempotency_key,
        )
        await _enqueue_import_quality_refresh(
            conn,
            user_id=user_id,
            import_job_id=import_job_id,
        )
        return

    existing_event_id = await _find_existing_event_id(
        conn,
        user_id=user_id,
        idempotency_key=plan.idempotency_key,
    )
    if existing_event_id is not None:
        await _update_import_job_status(
            conn,
            import_job_id=import_job_id,
            status="completed",
            receipt={
                **base_receipt,
                "write": {
                    "result": "idempotent_replay",
                    "event_id": existing_event_id,
                    "idempotency_key": plan.idempotency_key,
                },
            },
            source_identity_key=plan.source_identity_key,
            payload_fingerprint=plan.payload_fingerprint,
            idempotency_key=plan.idempotency_key,
        )
        await _enqueue_import_quality_refresh(
            conn,
            user_id=user_id,
            import_job_id=import_job_id,
        )
        return

    event_id = await _insert_external_activity_event(
        conn,
        user_id=user_id,
        import_job_id=import_job_id,
        provider=provider,
        idempotency_key=plan.idempotency_key,
        canonical_payload=plan.canonical_activity.model_dump(mode="json"),
    )
    await _update_import_job_status(
        conn,
        import_job_id=import_job_id,
        status="completed",
        receipt={
            **base_receipt,
            "write": {
                "result": "created",
                "event_id": event_id,
                "idempotency_key": plan.idempotency_key,
            },
        },
        source_identity_key=plan.source_identity_key,
        payload_fingerprint=plan.payload_fingerprint,
        idempotency_key=plan.idempotency_key,
    )

    logger.info(
        "external import completed job=%s provider=%s activity=%s event_id=%s",
        import_job_id,
        provider,
        external_activity_id,
        event_id,
    )
