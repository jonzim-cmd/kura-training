"""Shared constants for consistency inbox escalation protocol."""

from __future__ import annotations

CONSISTENCY_REVIEW_DECISION_EVENT_TYPE = "quality.consistency.review.decided"
CONSISTENCY_REVIEW_ALLOWED_DECISIONS = ("approve", "decline", "snooze")
CONSISTENCY_REVIEW_MAX_QUESTIONS_PER_TURN = 1
CONSISTENCY_REVIEW_DEFAULT_SNOOZE_HOURS = 72
CONSISTENCY_REVIEW_DECLINE_COOLDOWN_DAYS = 7
