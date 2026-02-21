"""Consistency Inbox — aggregates per-user data quality findings for chat surfacing.

This module builds the ``consistency_inbox/overview`` projection from
``quality.save_claim.checked`` signals, open ``quality_health`` issues, plus
prior ``quality.consistency.review.decided`` decisions. The projection is read
by the agent context endpoint so that the agent can proactively surface
findings and request explicit user decisions before applying fixes.

**Safety invariant**: No fix is executed without a prior ``approve`` decision
recorded via ``quality.consistency.review.decided``.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .utils import get_retracted_event_ids

logger = logging.getLogger(__name__)

CONSISTENCY_INBOX_SCHEMA_VERSION = 1
CONSISTENCY_INBOX_PROJECTION_TYPE = "consistency_inbox"
CONSISTENCY_INBOX_KEY = "overview"

# How far back to scan for quality signals when building the inbox.
_SCAN_WINDOW_DAYS = 30

# Severity ordering for ``highest_severity``.
_SEVERITY_ORDER = {"critical": 3, "warning": 2, "info": 1, "none": 0}

# Cooldown durations (applied server-side via prompt_control).
_COOLDOWN_AFTER_DECLINE_DAYS = 7
_DEFAULT_SNOOZE_HOURS = 72

_QUALITY_EVENT_TYPES = ("quality.save_claim.checked",)
_QUALITY_HEALTH_TO_INBOX_SEVERITY = {
    "high": "critical",
    "medium": "warning",
    "low": "info",
    "info": "info",
}


def _normalize_inbox_severity(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in _SEVERITY_ORDER:
        return text
    return _QUALITY_HEALTH_TO_INBOX_SEVERITY.get(text, "warning")


def _stable_item_id(user_id: str, signal_type: str, detail: str) -> str:
    """Generate a deterministic, stable item_id for dedup and cooldown tracking."""
    seed = f"{user_id}|{signal_type}|{detail}"
    h = hashlib.sha256(seed.encode()).hexdigest()[:16]
    return f"ci-{h}"


def _highest_severity(items: list[dict[str, Any]]) -> str:
    """Return the highest severity across all items."""
    if not items:
        return "none"
    return max(
        (item.get("severity", "info") for item in items),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
    )


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def build_consistency_inbox(
    quality_events: list[dict[str, Any]],
    user_id: str,
    decisions: list[dict[str, Any]] | None = None,
    quality_health_issues: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the consistency_inbox/overview projection data.

    Parameters
    ----------
    quality_events:
        Rows from events table matching quality.save_claim.checked within the
        scan window.
    user_id:
        The user for whom the inbox is being built.
    decisions:
        Prior quality.consistency.review.decided events for cooldown tracking.
    now:
        Current timestamp (injectable for testing).

    Returns
    -------
    dict suitable for UPSERT into the projections table.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    if decisions is None:
        decisions = []
    if quality_health_issues is None:
        quality_health_issues = []

    window_start = now - timedelta(days=_SCAN_WINDOW_DAYS)

    # Build a set of declined/snoozed item_ids with their cooldown-until timestamps.
    cooldown_map: dict[str, datetime] = {}
    for dec_event in decisions:
        data = dec_event.get("data") or {}
        decision = data.get("decision", "")
        item_ids = data.get("item_ids") or []
        ts = _as_utc_datetime(dec_event.get("timestamp"))
        if ts is None:
            continue
        for item_id in item_ids:
            item_id_text = str(item_id).strip()
            if not item_id_text:
                continue
            if decision == "decline":
                cooldown_map[item_id_text] = ts + timedelta(days=_COOLDOWN_AFTER_DECLINE_DAYS)
            elif decision == "snooze":
                snooze_str = data.get("snooze_until")
                if snooze_str:
                    parsed_snooze = _as_utc_datetime(snooze_str)
                    if parsed_snooze is None:
                        parsed_snooze = ts + timedelta(hours=_DEFAULT_SNOOZE_HOURS)
                    cooldown_map[item_id_text] = parsed_snooze
                else:
                    cooldown_map[item_id_text] = ts + timedelta(hours=_DEFAULT_SNOOZE_HOURS)
            elif decision == "approve":
                # Approved items are removed from cooldown (fix was applied).
                cooldown_map.pop(item_id_text, None)

    # Aggregate findings from save_claim mismatches.
    items: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()

    for row in quality_events:
        ts = _as_utc_datetime(row.get("timestamp"))
        if ts is None or ts < window_start:
            continue
        data = row.get("data") or {}
        event_type = row.get("event_type", "")

        if event_type == "quality.save_claim.checked":
            severity = data.get("mismatch_severity", "none")
            if severity in ("none",):
                continue
            weight = data.get("mismatch_weight", 0.0)
            if weight <= 0:
                continue
            reason_codes = data.get("mismatch_reason_codes") or []
            detail = "|".join(sorted(reason_codes)) if reason_codes else severity
            item_id = _stable_item_id(user_id, "save_claim_mismatch", detail)
            if item_id in seen_item_ids:
                continue
            seen_item_ids.add(item_id)

            # Check cooldown.
            if item_id in cooldown_map and cooldown_map[item_id] > now:
                continue

            summary = _build_mismatch_summary(severity, reason_codes)
            items.append({
                "item_id": item_id,
                "severity": severity,
                "summary": summary,
                "recommended_action": "Review and confirm the affected values.",
                "evidence_ref": str(row.get("id", "")),
                "first_seen": ts.isoformat(),
                "source_type": "save_claim_mismatch",
            })

    # Aggregate open quality_health issues so every unresolved health issue can
    # be surfaced proactively to the agent.
    for issue in quality_health_issues:
        issue_id = str(issue.get("issue_id") or "").strip()
        if not issue_id:
            continue

        item_id = _stable_item_id(user_id, "quality_health_issue", issue_id)
        if item_id in seen_item_ids:
            continue
        seen_item_ids.add(item_id)

        if item_id in cooldown_map and cooldown_map[item_id] > now:
            continue

        issue_severity = _normalize_inbox_severity(issue.get("severity"))
        issue_detail = str(issue.get("detail") or "").strip()
        summary = (
            issue_detail
            if issue_detail
            else f"Open quality health issue detected ({issue_id})."
        )
        issue_type = str(issue.get("type") or "").strip()
        proposal_state = str(issue.get("proposal_state") or "").strip().lower()
        detected_at = (
            _as_utc_datetime(issue.get("detected_at"))
            or _as_utc_datetime(issue.get("first_seen"))
            or now
        )

        if proposal_state == "simulated_safe":
            recommended_action = (
                "Offer deterministic repair approval or snooze if user wants to defer."
            )
        elif proposal_state == "simulated_risky":
            recommended_action = (
                "Ask for explicit user decision before any risky correction."
            )
        else:
            recommended_action = (
                "Ask for explicit decision (approve, decline, or snooze)."
            )

        items.append({
            "item_id": item_id,
            "severity": issue_severity,
            "summary": summary,
            "recommended_action": recommended_action,
            "evidence_ref": f"quality_health:{issue_id}",
            "first_seen": detected_at.isoformat(),
            "source_type": "quality_health_issue",
            "issue_id": issue_id,
            "issue_type": issue_type or None,
            "proposal_state": proposal_state or None,
        })

    # Sort by severity (critical first).
    items.sort(key=lambda i: -_SEVERITY_ORDER.get(i.get("severity", "info"), 0))

    # Escalate whenever unresolved items remain visible to the agent.
    requires_human_decision = len(items) > 0

    # Determine prompt_control.
    last_prompted_at: str | None = None
    cooldown_active = any(until > now for until in cooldown_map.values())
    snooze_until_dt: datetime | None = None
    sorted_decisions = sorted(
        decisions,
        key=lambda event: _as_utc_datetime(event.get("timestamp"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    for dec_event in sorted_decisions:
        dec_ts = _as_utc_datetime(dec_event.get("timestamp"))
        if dec_ts is not None:
            last_prompted_at = dec_ts.isoformat()
        dec_data = dec_event.get("data") or {}
        if dec_data.get("decision") == "snooze":
            snooze_dt = _as_utc_datetime(dec_data.get("snooze_until"))
            if snooze_dt is None and dec_ts is not None:
                snooze_dt = dec_ts + timedelta(hours=_DEFAULT_SNOOZE_HOURS)
            if snooze_dt is not None and (snooze_until_dt is None or snooze_dt > snooze_until_dt):
                snooze_until_dt = snooze_dt

    if snooze_until_dt is not None and snooze_until_dt > now:
        cooldown_active = True

    return {
        "schema_version": CONSISTENCY_INBOX_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "pending_items_total": len(items),
        "highest_severity": _highest_severity(items),
        "requires_human_decision": requires_human_decision,
        "items": items,
        "prompt_control": {
            "last_prompted_at": last_prompted_at,
            "snooze_until": snooze_until_dt.isoformat() if snooze_until_dt else None,
            "cooldown_active": cooldown_active,
            "max_quality_questions_per_turn": 1,
            "default_snooze_hours": _DEFAULT_SNOOZE_HOURS,
        },
    }


def _build_mismatch_summary(severity: str, reason_codes: list[str]) -> str:
    """Build a short, user-facing summary for a save_claim mismatch."""
    if "save_echo_missing" in reason_codes:
        return (
            "A recent entry was saved without echoing back the values. "
            "The stored data may not match what was intended."
        )
    if "save_echo_partial" in reason_codes:
        return (
            "A recent entry was saved with only partial value confirmation. "
            "Some stored values may need verification."
        )
    if "proof_verification_failed_but_echo_complete" in reason_codes:
        return (
            "A recent entry passed value echo but failed proof verification. "
            "This is likely a transient system issue, not a data problem."
        )
    if "proof_verification_failed" in reason_codes:
        return (
            "A recent entry failed proof verification. "
            "The stored data should be reviewed."
        )
    return f"A data quality issue was detected (severity: {severity})."


async def _load_quality_events(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, event_type, timestamp, data
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
              AND timestamp >= NOW() - INTERVAL '30 days'
            ORDER BY timestamp DESC, id DESC
            """,
            (user_id, list(_QUALITY_EVENT_TYPES)),
        )
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def _load_decision_events(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, event_type, timestamp, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'quality.consistency.review.decided'
            ORDER BY timestamp DESC, id DESC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def _load_quality_health_issues(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
) -> tuple[list[dict[str, Any]], str | None]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data, last_event_id
            FROM projections
            WHERE user_id = %s
              AND projection_type = 'quality_health'
              AND key = 'overview'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = await cur.fetchone()

    if not row:
        return [], None

    data = row.get("data")
    if not isinstance(data, dict):
        return [], str(row.get("last_event_id") or "").strip() or None

    issues_raw = data.get("issues")
    if not isinstance(issues_raw, list):
        return [], str(row.get("last_event_id") or "").strip() or None

    issues: list[dict[str, Any]] = []
    for issue in issues_raw:
        if not isinstance(issue, dict):
            continue
        status = str(issue.get("status") or "open").strip().lower()
        if status not in {"", "open"}:
            continue
        if not str(issue.get("issue_id") or "").strip():
            continue
        issues.append(issue)

    return issues, str(row.get("last_event_id") or "").strip() or None


def _non_retracted(
    rows: list[dict[str, Any]],
    retracted_ids: set[str],
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        row_id = row.get("id")
        if row_id is None:
            filtered.append(row)
            continue
        if str(row_id) in retracted_ids:
            continue
        filtered.append(row)
    return filtered


async def refresh_consistency_inbox_for_user(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    *,
    anchor_event_id: str | None = None,
) -> dict[str, Any]:
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    quality_rows = _non_retracted(await _load_quality_events(conn, user_id), retracted_ids)
    decision_rows = _non_retracted(await _load_decision_events(conn, user_id), retracted_ids)
    quality_health_issues, quality_health_last_event_id = await _load_quality_health_issues(
        conn,
        user_id,
    )

    if not quality_rows and not decision_rows and not quality_health_issues:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM projections
                WHERE user_id = %s
                  AND projection_type = %s
                  AND key = %s
                """,
                (user_id, CONSISTENCY_INBOX_PROJECTION_TYPE, CONSISTENCY_INBOX_KEY),
            )
        return {
            "status": "deleted",
            "pending_items_total": 0,
            "requires_human_decision": False,
        }

    now = datetime.now(tz=timezone.utc)
    projection_data = build_consistency_inbox(
        quality_rows,
        user_id,
        decisions=decision_rows,
        quality_health_issues=quality_health_issues,
        now=now,
    )
    latest_seen_id: Any = None
    if anchor_event_id:
        latest_seen_id = anchor_event_id
    elif quality_rows:
        latest_seen_id = quality_rows[0].get("id")
    elif quality_health_last_event_id:
        latest_seen_id = quality_health_last_event_id
    elif decision_rows:
        latest_seen_id = decision_rows[0].get("id")

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO projections (user_id, projection_type, key, data, version, last_event_id, updated_at)
            VALUES (%s, %s, %s, %s, 1, %s, NOW())
            ON CONFLICT (user_id, projection_type, key) DO UPDATE SET
                data = EXCLUDED.data,
                version = projections.version + 1,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = NOW()
            """,
            (
                user_id,
                CONSISTENCY_INBOX_PROJECTION_TYPE,
                CONSISTENCY_INBOX_KEY,
                Json(projection_data),
                str(latest_seen_id) if latest_seen_id else None,
            ),
        )

    return {
        "status": "updated",
        "pending_items_total": int(projection_data.get("pending_items_total", 0)),
        "requires_human_decision": bool(
            projection_data.get("requires_human_decision", False)
        ),
        "highest_severity": str(projection_data.get("highest_severity", "none")),
    }


async def refresh_all_consistency_inboxes(
    conn: psycopg.AsyncConnection[Any],
) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT user_id
            FROM (
                SELECT DISTINCT user_id
                FROM events
                WHERE event_type = ANY(%s)
                UNION
                SELECT DISTINCT user_id
                FROM projections
                WHERE projection_type = 'quality_health'
                  AND key = 'overview'
            ) AS users
            ORDER BY user_id
            """,
            (
                list(_QUALITY_EVENT_TYPES)
                + ["quality.consistency.review.decided"],
            ),
        )
        user_rows = await cur.fetchall()

    users = [str(row["user_id"]) for row in user_rows]
    updated = 0
    decisions_required = 0
    for user_id in users:
        result = await refresh_consistency_inbox_for_user(conn, user_id)
        if result["status"] == "updated":
            updated += 1
            if result["requires_human_decision"]:
                decisions_required += 1

    summary = {
        "status": "ok",
        "users_scanned": len(users),
        "projections_updated": updated,
        "users_requiring_decision": decisions_required,
    }
    logger.info(
        "Refreshed consistency_inbox projections: users=%d updated=%d requiring_decision=%d",
        summary["users_scanned"],
        summary["projections_updated"],
        summary["users_requiring_decision"],
    )
    return summary
