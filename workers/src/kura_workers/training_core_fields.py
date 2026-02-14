"""Training core-field registry and deterministic mention mapping (PDC.7)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_CORE_FIELD_REGISTRY: dict[str, dict[str, tuple[str, ...]]] = {
    "strength": {
        "required": ("exercise",),
        "optional": ("exercise_id", "weight_kg", "reps", "rpe", "rir", "set_type"),
        "mention_bound": ("rest_seconds", "tempo", "rir", "set_type"),
    },
    "hypertrophy": {
        "required": ("exercise",),
        "optional": ("exercise_id", "weight_kg", "reps", "rpe", "rir", "set_type"),
        "mention_bound": ("rest_seconds", "tempo", "rir", "set_type"),
    },
    "oly": {
        "required": ("exercise",),
        "optional": ("exercise_id", "weight_kg", "reps", "rpe", "rir", "set_type"),
        "mention_bound": ("rest_seconds", "tempo", "set_type"),
    },
}

_REST_WORD = (
    r"(?:"
    r"rest|pause|break|satzpause"              # EN / DE
    r"|repos|r[eé]cup(?:[eé]ration)?"          # FR
    r"|descanso|pausa"                          # ES / PT / IT
    r"|riposo"                                  # IT
    r"|отдых|пауза"                            # RU
    r"|pauze|rust"                              # NL
    r"|przerwa"                                 # PL
    r"|vila|paus"                               # SV / NO / DA
    r"|dinlenme|ara"                            # TR
    r")"
)
_UNIT_SECONDS = (
    r"(?:"
    r"seconds?|secondes?|secondi|secondo|seconden"  # EN / FR / IT / NL
    r"|secs?|sek(?:und[eny]?)?"                     # EN abbrev / DE / PL
    r"|seg(?:undos?)?"                               # ES / PT
    r"|секунд[аы]?|сек"                             # RU
    r"|saniye|sn"                                    # TR
    r"|s"                                            # universal (last — \b prevents substring matches)
    r")"
)
_UNIT_MINUTES = (
    r"(?:"
    r"minutes?|minuten|minutos?|minuti|minuut|minut[ey]?"  # EN / DE / ES / PT / IT / NL / PL
    r"|mins?|мин(?:ут[аы]?)?"                               # EN abbrev / RU
    r"|dakika|dk"                                            # TR
    r"|m"                                                    # universal (last — \b prevents substring matches)
    r")"
)
_TEMPO_RE = re.compile(r"\btempo\s*[:=]?\s*(\d-[\dx]-[\dx]-[\dx])\b", re.IGNORECASE)
_TEMPO_BARE_RE = re.compile(r"\b(\d-[\dx]-[\dx]-[\dx])\b", re.IGNORECASE)
_RIR_RE = re.compile(
    r"\b(?:rir\s*[:=]?\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*rir|(\d+)\s*reps?\s+in\s+reserve)\b",
    re.IGNORECASE,
)
_REST_MMSS_RE = re.compile(
    rf"\b{_REST_WORD}\s*[:=]?\s*(\d{{1,2}}):(\d{{2}})\b",
    re.IGNORECASE,
)
_REST_SECONDS_RE = re.compile(
    rf"\b(?:{_REST_WORD}\s*[:=]?\s*(\d{{1,3}})\s*{_UNIT_SECONDS}|(\d{{1,3}})\s*{_UNIT_SECONDS}\s*{_REST_WORD})\b",
    re.IGNORECASE,
)
_REST_MINUTES_RE = re.compile(
    rf"\b(?:{_REST_WORD}\s*[:=]?\s*(\d{{1,2}})\s*{_UNIT_MINUTES}|(\d{{1,2}})\s*{_UNIT_MINUTES}\s*{_REST_WORD})\b",
    re.IGNORECASE,
)
_REST_NUMBER_RE = re.compile(
    rf"\b{_REST_WORD}\s*[:=]?\s*(\d{{1,3}})\b",
    re.IGNORECASE,
)


def core_field_registry() -> dict[str, dict[str, tuple[str, ...]]]:
    return dict(_CORE_FIELD_REGISTRY)


def _normalize_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _normalize_rest_seconds(value: Any) -> float | None:
    parsed = _normalize_float(value)
    if parsed is None:
        return None
    if parsed < 0:
        return None
    return round(parsed, 2)


def _normalize_rir(value: Any) -> float | None:
    parsed = _normalize_float(value)
    if parsed is None:
        return None
    if parsed < 0:
        return 0.0
    if parsed > 10:
        return 10.0
    return round(parsed, 2)


def _normalize_set_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    for needle, canonical in (
        ("warmup", "warmup"),
        ("warm-up", "warmup"),
        ("backoff", "backoff"),
        ("back-off", "backoff"),
        ("amrap", "amrap"),
        ("working", "working"),
    ):
        if needle in text:
            return canonical
    return None


_CJK_REST_KEYWORDS = ("休憩", "レスト", "레스트", "휴식", "休息")
_CJK_SECONDS_RE = re.compile(r"(\d+)\s*[秒초]")
_CJK_MINUTES_RE = re.compile(r"(\d+)\s*[分분]")
_COMBINED_PRIME_RE = re.compile(r"(\d{1,2})'(\d{2})(?:''|\")")
_DOUBLE_PRIME_RE = re.compile(r"(\d{1,3})(?:''|\")")
_SINGLE_PRIME_RE = re.compile(r"(\d{1,2})'(?!\d)")


def _preprocess_time_text(text: str) -> str:
    """Normalize prime notation, CJK units, and international rest keywords."""
    # Unicode single-prime-like → ASCII apostrophe
    for ch in "\u2032\u02B9\u2018\u2019\u02BC\u00B4`":
        text = text.replace(ch, "'")
    # Unicode double-prime-like → ASCII double quote
    for ch in "\u2033\u201C\u201D":
        text = text.replace(ch, '"')

    # CJK rest keywords → "rest"
    for kw in _CJK_REST_KEYWORDS:
        text = text.replace(kw, "rest")

    # CJK time units → ASCII equivalents
    text = _CJK_SECONDS_RE.sub(r"\1 sec", text)
    text = _CJK_MINUTES_RE.sub(r"\1 min", text)

    # Combined prime: 1'30'' or 1'30" → 1:30 (must be before individual handling)
    text = _COMBINED_PRIME_RE.sub(r"\1:\2", text)
    # Double prime = seconds: 90'' or 90" → 90 sec
    text = _DOUBLE_PRIME_RE.sub(r"\1 sec", text)
    # Single prime = minutes: 2' → 2 min (lookahead prevents matching before digits)
    text = _SINGLE_PRIME_RE.sub(r"\1 min", text)

    return text


def _extract_rest_seconds(text: str) -> float | None:
    if not text:
        return None
    text = _preprocess_time_text(text)
    if match := _REST_MMSS_RE.search(text):
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        return _normalize_rest_seconds((minutes * 60) + seconds)
    if match := _REST_SECONDS_RE.search(text):
        raw = match.group(1) or match.group(2)
        return _normalize_rest_seconds(raw)
    if match := _REST_MINUTES_RE.search(text):
        raw = match.group(1) or match.group(2)
        parsed = _normalize_float(raw)
        if parsed is None:
            return None
        return _normalize_rest_seconds(parsed * 60.0)
    if match := _REST_NUMBER_RE.search(text):
        # Bare "pause 90" falls back to seconds.
        return _normalize_rest_seconds(match.group(1))
    return None


def extract_set_context_mentions(text: str) -> dict[str, Any]:
    """Deterministically map free text mentions to structured set-context fields."""
    normalized_text = str(text or "").strip().lower()
    if not normalized_text:
        return {}

    mentions: dict[str, Any] = {}
    rest_seconds = _extract_rest_seconds(normalized_text)
    if rest_seconds is not None:
        mentions["rest_seconds"] = rest_seconds

    if match := _RIR_RE.search(normalized_text):
        rir_value = match.group(1) or match.group(2) or match.group(3)
        parsed_rir = _normalize_rir(rir_value)
        if parsed_rir is not None:
            mentions["rir"] = parsed_rir

    tempo_match = _TEMPO_RE.search(normalized_text) or _TEMPO_BARE_RE.search(
        normalized_text
    )
    if tempo_match:
        mentions["tempo"] = tempo_match.group(1).lower()

    set_type = _normalize_set_type(normalized_text)
    if set_type is not None:
        mentions["set_type"] = set_type

    return mentions


def _extract_payload_mentions(data: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    mentions: dict[str, Any] = {}
    for candidate in (
        data.get("notes"),
        data.get("context_text"),
        data.get("utterance"),
        metadata.get("source_text"),
        metadata.get("raw_text"),
        metadata.get("user_message"),
    ):
        if not candidate:
            continue
        for key, value in extract_set_context_mentions(str(candidate)).items():
            mentions.setdefault(key, value)

    # Explicit values in set payload count as mention-bound capture.
    explicit_rest = _normalize_rest_seconds(data.get("rest_seconds"))
    if explicit_rest is not None:
        mentions["rest_seconds"] = explicit_rest

    explicit_rir = _normalize_rir(data.get("rir"))
    if explicit_rir is not None:
        mentions["rir"] = explicit_rir

    if isinstance(data.get("tempo"), str) and data["tempo"].strip():
        mentions["tempo"] = data["tempo"].strip().lower()

    explicit_set_type = _normalize_set_type(data.get("set_type"))
    if explicit_set_type is not None:
        mentions["set_type"] = explicit_set_type

    return mentions


def _normalized_modality(data: dict[str, Any]) -> str:
    raw = str(data.get("modality") or data.get("training_modality") or "").lower()
    if raw in _CORE_FIELD_REGISTRY:
        return raw
    return "strength"


def _normalize_exercise_scope(data: dict[str, Any]) -> str:
    value = str(data.get("exercise_id") or data.get("exercise") or "").strip().lower()
    return value or "*"


def _normalize_session_scope(
    metadata: dict[str, Any],
    timestamp: Any,
) -> str:
    raw_session = str(metadata.get("session_id") or "").strip()
    if raw_session:
        return raw_session

    if isinstance(timestamp, datetime):
        return timestamp.date().isoformat()
    if isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp)
            return parsed.date().isoformat()
        except ValueError:
            return "unknown-session"
    return "unknown-session"


def evaluate_set_context_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply mention defaults per session+exercise and flag missing persisted fields."""
    defaults_by_scope: dict[tuple[str, str], dict[str, Any]] = {}
    evaluations: list[dict[str, Any]] = []

    for row in rows:
        data = row.get("data") or {}
        metadata = row.get("metadata") or {}
        modality = _normalized_modality(data)
        mention_bound_fields = set(
            _CORE_FIELD_REGISTRY[modality].get("mention_bound", ())
        )
        session_scope = _normalize_session_scope(metadata, row.get("timestamp"))
        exercise_scope = _normalize_exercise_scope(data)
        scope = (session_scope, exercise_scope)
        current_defaults = dict(defaults_by_scope.get(scope, {}))

        mentions = _extract_payload_mentions(data, metadata)
        for field in mention_bound_fields:
            if field in mentions:
                current_defaults[field] = mentions[field]

        # Explicit structured values always override defaults.
        if data.get("rest_seconds") is not None:
            normalized = _normalize_rest_seconds(data.get("rest_seconds"))
            if normalized is not None:
                current_defaults["rest_seconds"] = normalized
        if data.get("rir") is not None:
            normalized = _normalize_rir(data.get("rir"))
            if normalized is not None:
                current_defaults["rir"] = normalized
        if isinstance(data.get("tempo"), str) and data["tempo"].strip():
            current_defaults["tempo"] = data["tempo"].strip().lower()
        if data.get("set_type") is not None:
            normalized = _normalize_set_type(data.get("set_type"))
            if normalized is not None:
                current_defaults["set_type"] = normalized

        missing_fields: list[str] = []
        hint_messages: list[str] = []
        for field in sorted(mention_bound_fields):
            if field not in current_defaults:
                continue
            if data.get(field) is not None:
                continue
            missing_fields.append(field)
            if field == "rest_seconds":
                hint_messages.append(
                    "Persist rest_seconds from mention/default to avoid loss (e.g. pause 90 sec)."
                )
            elif field == "tempo":
                hint_messages.append(
                    "Persist tempo when mentioned so subsequent sets inherit correctly."
                )
            elif field == "rir":
                hint_messages.append(
                    "Persist RIR when mentioned; do not keep it only in narrative text."
                )
            elif field == "set_type":
                hint_messages.append(
                    "Persist set_type when mention indicates warmup/backoff/amrap context."
                )

        defaults_by_scope[scope] = current_defaults
        evaluations.append(
            {
                "event_id": str(row.get("id", "")),
                "session_scope": session_scope,
                "exercise_scope": exercise_scope,
                "modality": modality,
                "mentioned_fields": {
                    key: value
                    for key, value in mentions.items()
                    if key in mention_bound_fields
                },
                "effective_defaults": {
                    key: value
                    for key, value in current_defaults.items()
                    if key in mention_bound_fields
                },
                "missing_fields": missing_fields,
                "hint_messages": hint_messages,
            }
        )

    return evaluations
