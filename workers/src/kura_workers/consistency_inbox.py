"""Consistency Inbox — aggregates per-user data quality findings for chat surfacing.

This module builds the ``consistency_inbox/overview`` projection from
quality signals (``quality.save_claim.checked``, ``learning.signal.logged``,
``quality_health/overview``).  The projection is read by the agent context
endpoint so that the agent can proactively surface findings and request
explicit user decisions before applying fixes.

**Safety invariant**: No fix is executed without a prior ``approve`` decision
recorded via ``quality.consistency.review.decided``.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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


def build_consistency_inbox(
    quality_events: list[dict[str, Any]],
    user_id: str,
    decisions: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the consistency_inbox/overview projection data.

    Parameters
    ----------
    quality_events:
        Rows from events table matching quality.save_claim.checked or
        learning.signal.logged within the scan window.
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

    window_start = now - timedelta(days=_SCAN_WINDOW_DAYS)

    # Build a set of declined/snoozed item_ids with their cooldown-until timestamps.
    cooldown_map: dict[str, datetime] = {}
    for dec_event in decisions:
        data = dec_event.get("data") or {}
        decision = data.get("decision", "")
        item_ids = data.get("item_ids") or []
        ts = dec_event.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        for item_id in item_ids:
            if decision == "decline":
                cooldown_map[item_id] = ts + timedelta(days=_COOLDOWN_AFTER_DECLINE_DAYS)
            elif decision == "snooze":
                snooze_str = data.get("snooze_until")
                if snooze_str:
                    try:
                        cooldown_map[item_id] = datetime.fromisoformat(snooze_str)
                    except (ValueError, TypeError):
                        cooldown_map[item_id] = ts + timedelta(hours=_DEFAULT_SNOOZE_HOURS)
                else:
                    cooldown_map[item_id] = ts + timedelta(hours=_DEFAULT_SNOOZE_HOURS)
            elif decision == "approve":
                # Approved items are removed from cooldown (fix was applied).
                cooldown_map.pop(item_id, None)

    # Aggregate findings from save_claim mismatches.
    items: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()

    for row in quality_events:
        ts = row.get("timestamp")
        if not isinstance(ts, datetime) or ts < window_start:
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
                "evidence_ref": row.get("id", ""),
                "first_seen": ts.isoformat(),
            })

    # Sort by severity (critical first).
    items.sort(key=lambda i: -_SEVERITY_ORDER.get(i.get("severity", "info"), 0))

    requires_human_decision = any(
        i["severity"] in ("critical", "warning") for i in items
    )

    # Determine prompt_control.
    last_prompted_at = None
    cooldown_active = False
    snooze_until = None
    for dec_event in sorted(decisions, key=lambda d: d.get("timestamp", now)):
        dec_data = dec_event.get("data") or {}
        if dec_data.get("decision") == "snooze":
            snooze_str = dec_data.get("snooze_until")
            if snooze_str:
                try:
                    snooze_until = datetime.fromisoformat(snooze_str).isoformat()
                    if datetime.fromisoformat(snooze_str) > now:
                        cooldown_active = True
                except (ValueError, TypeError):
                    pass

    return {
        "schema_version": CONSISTENCY_INBOX_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "pending_items_total": len(items),
        "highest_severity": _highest_severity(items),
        "requires_human_decision": requires_human_decision,
        "items": items,
        "prompt_control": {
            "last_prompted_at": last_prompted_at,
            "snooze_until": snooze_until,
            "cooldown_active": cooldown_active,
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
