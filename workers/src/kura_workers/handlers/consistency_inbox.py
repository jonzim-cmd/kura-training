"""Projection handler for consistency_inbox/overview."""

from __future__ import annotations

from typing import Any

import psycopg

from ..consistency_inbox import refresh_consistency_inbox_for_user
from ..registry import projection_handler


@projection_handler(
    "quality.save_claim.checked",
    "quality.consistency.review.decided",
)
async def update_consistency_inbox(
    conn: psycopg.AsyncConnection[Any],
    payload: dict[str, Any],
) -> None:
    user_id = str(payload["user_id"])
    anchor_event_id = str(payload.get("event_id") or "").strip() or None
    await refresh_consistency_inbox_for_user(
        conn,
        user_id,
        anchor_event_id=anchor_event_id,
    )
