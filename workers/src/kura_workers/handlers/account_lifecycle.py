"""Account lifecycle handlers (soft-delete grace period -> hard delete)."""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from ..registry import register

logger = logging.getLogger(__name__)


def _parse_requested_at(raw_value: Any) -> datetime | None:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


@register("account.hard_delete")
async def handle_account_hard_delete(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    user_id_raw = payload.get("user_id")
    if not user_id_raw:
        raise ValueError("Missing user_id in account.hard_delete payload")

    try:
        user_id = str(UUID(str(user_id_raw)))
    except ValueError as exc:
        raise ValueError("Invalid user_id in account.hard_delete payload") from exc

    requested_at_payload = _parse_requested_at(payload.get("deletion_requested_at"))

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT is_active, deletion_requested_at, deletion_scheduled_for
            FROM users
            WHERE id = %s
            FOR UPDATE
            """,
            (user_id,),
        )
        row = await cur.fetchone()

    if not row:
        logger.info("account.hard_delete skipped: user %s no longer exists", user_id)
        return

    if row["is_active"]:
        logger.info("account.hard_delete skipped: user %s is active again", user_id)
        return

    db_requested_at = row["deletion_requested_at"]
    db_scheduled_for = row["deletion_scheduled_for"]
    if db_requested_at is None or db_scheduled_for is None:
        logger.info(
            "account.hard_delete skipped: user %s has no active deletion window", user_id
        )
        return

    now = datetime.now(timezone.utc)
    if db_scheduled_for > now:
        logger.info(
            "account.hard_delete skipped: user %s deletion_scheduled_for=%s still in future",
            user_id,
            db_scheduled_for,
        )
        return

    if requested_at_payload is not None:
        # Second-level comparison avoids false negatives from serialization precision.
        if db_requested_at.replace(microsecond=0) != requested_at_payload.replace(microsecond=0):
            logger.info(
                "account.hard_delete skipped: stale deletion job for user %s (payload=%s, db=%s)",
                user_id,
                requested_at_payload,
                db_requested_at,
            )
            return

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT events_deleted, projections_deleted FROM delete_user_account(%s)",
            (user_id,),
        )
        counts = await cur.fetchone()

    logger.info(
        "account.hard_delete executed for user %s (events_deleted=%s projections_deleted=%s)",
        user_id,
        counts["events_deleted"] if counts else None,
        counts["projections_deleted"] if counts else None,
    )
