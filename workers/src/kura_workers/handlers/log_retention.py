"""Scheduled retention cleanup for operational and security logs."""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ..registry import register

logger = logging.getLogger(__name__)

API_ACCESS_LOG_RETENTION_DAYS = 30
SECURITY_ABUSE_RETENTION_DAYS = 90
KILL_SWITCH_AUDIT_RETENTION_DAYS = 365
SUPPORT_AUDIT_RETENTION_DAYS = 730
PASSWORD_RESET_TOKEN_RETENTION_DAYS = 30


async def _delete_with_days_window(
    conn: psycopg.AsyncConnection[Any],
    table: str,
    timestamp_column: str,
    days: int,
) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            f"DELETE FROM {table} WHERE {timestamp_column} < NOW() - make_interval(days => %s)",
            (days,),
        )
        return cur.rowcount


@register("maintenance.log_retention")
async def handle_log_retention(
    conn: psycopg.AsyncConnection[Any], payload: dict[str, Any]
) -> None:
    """Delete aged operational records according to the initial legal retention matrix."""
    deleted_api_access = await _delete_with_days_window(
        conn,
        table="api_access_log",
        timestamp_column="timestamp",
        days=API_ACCESS_LOG_RETENTION_DAYS,
    )
    deleted_security_abuse = await _delete_with_days_window(
        conn,
        table="security_abuse_telemetry",
        timestamp_column="timestamp",
        days=SECURITY_ABUSE_RETENTION_DAYS,
    )
    deleted_kill_switch = await _delete_with_days_window(
        conn,
        table="security_kill_switch_audit",
        timestamp_column="timestamp",
        days=KILL_SWITCH_AUDIT_RETENTION_DAYS,
    )
    deleted_support_access = await _delete_with_days_window(
        conn,
        table="support_access_audit",
        timestamp_column="created_at",
        days=SUPPORT_AUDIT_RETENTION_DAYS,
    )

    async with conn.cursor() as cur:
        await cur.execute(
            """
            DELETE FROM password_reset_tokens
            WHERE
                (used_at IS NOT NULL AND used_at < NOW() - make_interval(days => %s))
                OR expires_at < NOW() - make_interval(days => %s)
            """,
            (
                PASSWORD_RESET_TOKEN_RETENTION_DAYS,
                PASSWORD_RESET_TOKEN_RETENTION_DAYS,
            ),
        )
        deleted_password_reset_tokens = cur.rowcount

    summary = {
        "scheduler_payload": payload,
        "retention_days": {
            "api_access_log": API_ACCESS_LOG_RETENTION_DAYS,
            "security_abuse_telemetry": SECURITY_ABUSE_RETENTION_DAYS,
            "security_kill_switch_audit": KILL_SWITCH_AUDIT_RETENTION_DAYS,
            "support_access_audit": SUPPORT_AUDIT_RETENTION_DAYS,
            "password_reset_tokens": PASSWORD_RESET_TOKEN_RETENTION_DAYS,
        },
        "deleted_rows": {
            "api_access_log": deleted_api_access,
            "security_abuse_telemetry": deleted_security_abuse,
            "security_kill_switch_audit": deleted_kill_switch,
            "support_access_audit": deleted_support_access,
            "password_reset_tokens": deleted_password_reset_tokens,
        },
    }

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            INSERT INTO log_retention_runs (started_at, completed_at, status, details)
            VALUES (NOW(), NOW(), 'completed', %s)
            RETURNING id
            """,
            (Json(summary),),
        )
        row = await cur.fetchone()
        run_id = int(row["id"]) if row is not None else None

    logger.info("maintenance.log_retention completed (run_id=%s, summary=%s)", run_id, summary)
