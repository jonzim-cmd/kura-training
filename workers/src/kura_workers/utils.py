"""Shared utility functions for Kura workers."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

DEFAULT_ASSUMED_TIMEZONE = "UTC"
TIMEZONE_ASSUMPTION_DISCLOSURE = (
    "No explicit timezone preference found; using UTC until the user confirms one."
)
TEMPORAL_DRIFT_THRESHOLD_SECONDS = 300
SESSION_BOUNDARY_OVERNIGHT_GAP_HOURS = 3.0
SESSION_BOUNDARY_MAX_DURATION_HOURS = 8.0

_TEMPORAL_TIMEZONE_FIELDS: tuple[str, ...] = (
    "source_timezone",
    "provider_timezone",
    "device_timezone",
    "timezone",
    "time_zone",
)
_TEMPORAL_PROVIDER_TIMESTAMP_FIELDS: tuple[str, ...] = (
    "source_timestamp_utc",
    "provider_timestamp_utc",
    "source_timestamp",
    "provider_timestamp",
    "occurred_at",
    "started_at",
    "start_time_utc",
    "start_time",
)
_TEMPORAL_DEVICE_TIMESTAMP_FIELDS: tuple[str, ...] = (
    "device_timestamp",
    "device_time",
    "device_local_time",
    "recorded_at_local",
)
_TEMPORAL_GENERIC_TIMESTAMP_FIELDS: tuple[str, ...] = ("timestamp",)


@dataclass(frozen=True)
class TemporalPoint:
    """Canonical temporal representation for projection grouping."""

    timestamp_utc: datetime
    local_date: date
    iso_week: str
    source: str
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class SessionBoundaryState:
    """State for fallback session grouping without explicit session_id."""

    session_key: str
    session_start_utc: datetime
    last_event_utc: datetime
    last_local_date: date


# ---------------------------------------------------------------------------
# Strength estimation
# ---------------------------------------------------------------------------


def epley_1rm(weight_kg: float, reps: int) -> float:
    """Estimate 1RM using the Epley formula. Returns 0 for invalid inputs."""
    if reps <= 0 or weight_kg <= 0:
        return 0.0
    if reps == 1:
        return weight_kg
    return weight_kg * (1 + reps / 30)


# ---------------------------------------------------------------------------
# Adaptive Projection helpers (Decision 10, Phase 1: Graceful Degradation)
# ---------------------------------------------------------------------------


def separate_known_unknown(
    data: dict[str, Any], known_fields: set[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split event data into known (handler-processed) and unknown (passthrough) fields.

    Returns (known, unknown). Unknown fields are preserved in projections
    so the agent can access them even if no handler logic exists yet.
    """
    known: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    for key, value in data.items():
        if key in known_fields:
            known[key] = value
        else:
            unknown[key] = value
    return known, unknown


def merge_observed_attributes(
    accumulator: dict[str, dict[str, int]],
    event_type: str,
    new_unknown: dict[str, Any],
) -> None:
    """Track frequency of unknown fields per event type (mutates accumulator).

    Structure: {event_type: {field: count}}. This allows Phase 2 pattern
    detection to know exactly which event type a novel field came from —
    critical for multi-event-type handlers like recovery (sleep + soreness + energy).
    """
    if not new_unknown:
        return
    if event_type not in accumulator:
        accumulator[event_type] = {}
    bucket = accumulator[event_type]
    for key in new_unknown:
        bucket[key] = bucket.get(key, 0) + 1


def check_expected_fields(
    data: dict[str, Any], expected: dict[str, str]
) -> list[dict[str, Any]]:
    """Return data_quality hints for missing expected fields.

    ``expected`` maps field names to human-readable hint messages, e.g.
    {"weight_kg": "No weight — bodyweight exercise?"}.
    """
    return [
        {"type": "missing_expected_field", "field": field, "hint": hint}
        for field, hint in expected.items()
        if field not in data
    ]


def normalize_timezone_name(value: Any) -> str | None:
    """Normalize timezone preference and verify it's a valid IANA name."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.upper() == "UTC":
        return "UTC"
    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return None
    return raw


def resolve_timezone_context(timezone_pref: Any) -> dict[str, Any]:
    """Return timezone context with explicit assumption disclosure when missing."""
    normalized = normalize_timezone_name(timezone_pref)
    if normalized:
        return {
            "timezone": normalized,
            "source": "preference",
            "assumed": False,
            "assumption_disclosure": None,
        }
    return {
        "timezone": DEFAULT_ASSUMED_TIMEZONE,
        "source": "assumed_default",
        "assumed": True,
        "assumption_disclosure": TIMEZONE_ASSUMPTION_DISCLOSURE,
    }


def local_date_for_timezone(ts: datetime, timezone_name: str) -> date:
    """Project event timestamp into the configured local date."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ZoneInfo(timezone_name)).date()


def _iso_week(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _parse_temporal_timestamp(
    value: Any,
    timezone_hint: str | None,
) -> tuple[datetime, bool] | None:
    if isinstance(value, datetime):
        return _as_utc(value), False

    raw: str | None = None
    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 1_000_000_000_000:
            epoch /= 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc), False
    if isinstance(value, str):
        raw = value.strip()
    if not raw:
        return None

    numeric = raw.replace(".", "", 1)
    if numeric.isdigit():
        epoch = float(raw)
        if epoch > 1_000_000_000_000:
            epoch /= 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc), False

    normalized_raw = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized_raw)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        if not timezone_hint:
            return None
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_hint))
        return parsed.astimezone(timezone.utc), True
    return parsed.astimezone(timezone.utc), False


def _extract_timezone_hint(data: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    for container in (metadata, data):
        for field in _TEMPORAL_TIMEZONE_FIELDS:
            normalized = normalize_timezone_name(container.get(field))
            if normalized:
                return normalized
    return None


def _pick_timestamp_candidate(
    data: dict[str, Any],
    metadata: dict[str, Any],
    fields: tuple[str, ...],
    timezone_hint: str | None,
) -> tuple[datetime, str, bool] | None:
    for container_name, container in (("metadata", metadata), ("data", data)):
        for field in fields:
            parsed = _parse_temporal_timestamp(container.get(field), timezone_hint)
            if parsed is None:
                continue
            ts_utc, assumed_timezone = parsed
            return ts_utc, f"{container_name}.{field}", assumed_timezone
    return None


def normalize_temporal_point(
    event_timestamp: datetime,
    *,
    timezone_name: str,
    data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    drift_threshold_seconds: int = TEMPORAL_DRIFT_THRESHOLD_SECONDS,
) -> TemporalPoint:
    """Resolve canonical timestamp/day/week with drift-aware conflict tags."""
    data_payload = data or {}
    metadata_payload = metadata or {}
    event_ts_utc = _as_utc(event_timestamp)
    timezone_hint = _extract_timezone_hint(data_payload, metadata_payload)

    provider_candidate = _pick_timestamp_candidate(
        data_payload,
        metadata_payload,
        _TEMPORAL_PROVIDER_TIMESTAMP_FIELDS,
        timezone_hint,
    )
    device_candidate = _pick_timestamp_candidate(
        data_payload,
        metadata_payload,
        _TEMPORAL_DEVICE_TIMESTAMP_FIELDS,
        timezone_hint,
    )
    generic_candidate = _pick_timestamp_candidate(
        data_payload,
        metadata_payload,
        _TEMPORAL_GENERIC_TIMESTAMP_FIELDS,
        timezone_hint,
    )

    chosen = provider_candidate or device_candidate or generic_candidate
    if chosen is None:
        canonical_ts_utc = event_ts_utc
        source = "event.timestamp"
        assumed_timezone = False
    else:
        canonical_ts_utc, source, assumed_timezone = chosen

    threshold_seconds = max(int(drift_threshold_seconds), 0)
    conflicts: list[str] = []

    if (
        provider_candidate is not None
        and device_candidate is not None
        and abs(
            (provider_candidate[0] - device_candidate[0]).total_seconds()
        ) > threshold_seconds
    ):
        conflicts.append("provider_device_drift")

    if source != "event.timestamp" and abs(
        (canonical_ts_utc - event_ts_utc).total_seconds()
    ) > threshold_seconds:
        conflicts.append("event_store_drift")

    if assumed_timezone:
        conflicts.append("naive_timestamp_assumed_timezone")

    local_day = local_date_for_timezone(canonical_ts_utc, timezone_name)
    return TemporalPoint(
        timestamp_utc=canonical_ts_utc,
        local_date=local_day,
        iso_week=_iso_week(local_day),
        source=source,
        conflicts=tuple(dict.fromkeys(conflicts)),
    )


def next_fallback_session_key(
    *,
    local_date: date,
    timestamp_utc: datetime,
    state: SessionBoundaryState | None,
    overnight_gap_hours: float = SESSION_BOUNDARY_OVERNIGHT_GAP_HOURS,
    max_session_hours: float = SESSION_BOUNDARY_MAX_DURATION_HOURS,
) -> tuple[str, SessionBoundaryState]:
    """Infer session key when no explicit session_id exists.

    Backward compatibility default stays day-based. We only keep a session across
    midnight when events are close in time and the whole inferred session does
    not exceed max_session_hours.
    """
    ts_utc = _as_utc(timestamp_utc)
    day_key = local_date.isoformat()

    if state is None:
        next_state = SessionBoundaryState(
            session_key=day_key,
            session_start_utc=ts_utc,
            last_event_utc=ts_utc,
            last_local_date=local_date,
        )
        return day_key, next_state

    if local_date == state.last_local_date:
        next_state = SessionBoundaryState(
            session_key=state.session_key,
            session_start_utc=state.session_start_utc,
            last_event_utc=ts_utc,
            last_local_date=local_date,
        )
        return state.session_key, next_state

    overnight_gap = timedelta(hours=max(overnight_gap_hours, 0.0))
    max_duration = timedelta(hours=max(max_session_hours, 0.0))
    gap = ts_utc - state.last_event_utc
    duration = ts_utc - state.session_start_utc

    if gap <= overnight_gap and duration <= max_duration:
        next_state = SessionBoundaryState(
            session_key=state.session_key,
            session_start_utc=state.session_start_utc,
            last_event_utc=ts_utc,
            last_local_date=local_date,
        )
        return state.session_key, next_state

    next_state = SessionBoundaryState(
        session_key=day_key,
        session_start_utc=ts_utc,
        last_event_utc=ts_utc,
        last_local_date=local_date,
    )
    return day_key, next_state


async def load_timezone_preference(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    retracted_ids: set[str],
) -> str | None:
    """Load latest non-retracted timezone preference for day/week semantics."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'preference.set'
              AND data->>'key' IN ('timezone', 'time_zone')
            ORDER BY timestamp DESC, id DESC
            LIMIT 64
            """,
            (user_id,),
        )
        pref_rows = await cur.fetchall()

    for row in pref_rows:
        if str(row["id"]) in retracted_ids:
            continue
        data = row.get("data") or {}
        normalized = normalize_timezone_name(data.get("value"))
        if normalized:
            return normalized
    return None


async def get_retracted_event_ids(
    conn: psycopg.AsyncConnection[Any], user_id: str
) -> set[str]:
    """Return set of event IDs that have been retracted by event.retracted events.

    Called once per handler invocation. Every handler uses this to filter
    retracted events from its full replay. Retractions are rare, so the
    set is typically empty — but filtering must happen on every call to
    handle the case where a retraction occurred between normal events.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data->>'retracted_event_id' AS retracted_id
            FROM events
            WHERE user_id = %s
              AND event_type = 'event.retracted'
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    return {row["retracted_id"] for row in rows if row["retracted_id"]}


def resolve_exercise_key(data: dict[str, Any]) -> str | None:
    """Resolve the canonical exercise key from event data.

    Prefers exercise_id (canonical) over exercise (free text).
    Both are normalized to lowercase with whitespace stripped.
    """
    exercise_id = data.get("exercise_id", "").strip().lower()
    if exercise_id:
        return exercise_id

    exercise = data.get("exercise", "").strip().lower()
    if exercise:
        return exercise

    return None


async def get_alias_map(
    conn: psycopg.AsyncConnection[Any],
    user_id: str,
    retracted_ids: set[str] | None = None,
) -> dict[str, str]:
    """Build alias → canonical target map from exercise.alias_created events.

    Returns {alias_lower: target_lower}. Direct event query, no cross-projection dependency.
    Confidence field intentionally omitted — this is for resolution only.
    See user_profile projection for full alias metadata (target + confidence).

    If retracted_ids is provided, excludes those events from the map.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, data
            FROM events
            WHERE user_id = %s
              AND event_type = 'exercise.alias_created'
            ORDER BY timestamp ASC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()

    alias_map: dict[str, str] = {}
    for row in rows:
        if retracted_ids and str(row["id"]) in retracted_ids:
            continue
        data = row["data"]
        alias = data.get("alias", "").strip().lower()
        target = data.get("exercise_id", "").strip().lower()
        if alias and target:
            alias_map[alias] = target
    return alias_map


def resolve_through_aliases(key: str, alias_map: dict[str, str]) -> str:
    """Single lookup: return canonical target or key unchanged. No chains."""
    return alias_map.get(key, key)


def find_all_keys_for_canonical(
    canonical: str, alias_map: dict[str, str]
) -> set[str]:
    """Return canonical + all aliases pointing to it."""
    keys = {canonical}
    for alias, target in alias_map.items():
        if target == canonical:
            keys.add(alias)
    return keys
