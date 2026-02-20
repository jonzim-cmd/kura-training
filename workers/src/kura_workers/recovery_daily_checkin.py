"""Normalization helpers for recovery.daily_checkin payloads.

Supports explicit structured fields and compact fast-input formats.
"""

from __future__ import annotations

import re
from typing import Any


_NUMERIC_FIELDS: tuple[str, ...] = (
    "bodyweight_kg",
    "sleep_hours",
    "soreness",
    "motivation",
    "hrv_rmssd",
    "sleep_quality",
    "physical_condition",
    "lifestyle_stability",
)

_ALIASES: dict[str, tuple[str, ...]] = {
    "bodyweight_kg": ("bodyweight_kg", "bodyweight", "weight_kg", "weight", "bw"),
    "sleep_hours": ("sleep_hours", "sleep", "sleep_duration", "sl"),
    "soreness": ("soreness", "sore", "doms", "sor"),
    "motivation": ("motivation", "mot", "drive"),
    "hrv_rmssd": ("hrv_rmssd", "hrv", "rmssd"),
    "sleep_quality": ("sleep_quality", "sq"),
    "physical_condition": ("physical_condition", "condition", "pc"),
    "lifestyle_stability": ("lifestyle_stability", "lifestyle", "ls"),
    "traveling_yesterday": (
        "traveling_yesterday",
        "travelled_yesterday",
        "traveled_yesterday",
        "traveling",
        "travel",
    ),
    "sick_today": ("sick_today", "sick", "ill_today", "ill"),
    "alcohol_last_night": ("alcohol_last_night", "alcohol", "alc"),
    "training_yesterday": ("training_yesterday", "yesterday_training", "training", "ty"),
    "supplements": ("supplements", "supplement"),
    "notes": ("notes", "note", "context", "comment"),
    "compact_input": ("compact_input", "compact", "input"),
}

_POSITIONAL_FIELDS: tuple[str, ...] = (
    "bodyweight_kg",
    "sleep_hours",
    "soreness",
    "motivation",
    "hrv_rmssd",
    "sick_today",
    "traveling_yesterday",
    "alcohol_last_night",
    "sleep_quality",
    "physical_condition",
    "lifestyle_stability",
)

_KV_RE = re.compile(r"^\s*([a-zA-Z_]+)\s*[:=]\s*(.*?)\s*$")


def _parse_decimal(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    if "," in raw and "." in raw:
        comma_idx = raw.rfind(",")
        dot_idx = raw.rfind(".")
        if comma_idx > dot_idx:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")

    try:
        return float(raw)
    except ValueError:
        return None


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0.0
    if not isinstance(value, str):
        return None

    raw = value.strip().lower()
    if not raw:
        return None
    if raw in {"true", "yes", "y", "ja", "j", "1"}:
        return True
    if raw in {"false", "no", "n", "nein", "0"}:
        return False
    return None


def _parse_alcohol(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        as_int = int(round(float(value)))
        if as_int <= 0:
            return "none"
        if as_int == 1:
            return "little"
        return "too_much"
    if not isinstance(value, str):
        return None

    raw = value.strip().lower()
    if not raw:
        return None
    if raw in {"none", "no", "0", "false", "kein", "keins", "nein"}:
        return "none"
    if raw in {"little", "a_little", "some", "moderat", "moderate", "1", "yes", "true"}:
        return "little"
    if raw in {"too_much", "much", "high", "2"}:
        return "too_much"
    return None


def _parse_training_tag(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        as_int = int(round(float(value)))
        if as_int <= 0:
            return "rest"
        if as_int == 1:
            return "easy"
        if as_int == 2:
            return "average"
        return "hard"
    if not isinstance(value, str):
        return None

    raw = value.strip().lower()
    if not raw:
        return None
    if raw in {"rest", "off", "none", "0"}:
        return "rest"
    if raw in {"easy", "light", "1"}:
        return "easy"
    if raw in {"average", "moderate", "normal", "2"}:
        return "average"
    if raw in {"hard", "heavy", "3"}:
        return "hard"
    return None


def _to_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text else None


def _normalize_supplements(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            token = item.strip()
            if token:
                result.append(token)
        return result or None
    if not isinstance(value, str):
        return None
    tokens = [token.strip() for token in re.split(r"[;,]", value) if token.strip()]
    return tokens or None


def _normalized_key_map(data: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        normalized[key.strip().lower()] = value
    return normalized


def _first_raw_value(data: dict[str, Any], field: str) -> Any:
    for alias in _ALIASES.get(field, (field,)):
        if alias in data:
            return data[alias]
    return None


def _canonicalize_compact_key(raw_key: str) -> str | None:
    key = raw_key.strip().lower()
    for field, aliases in _ALIASES.items():
        if key in aliases:
            return field
    return None


def _parse_compact_key_value(raw_compact: str) -> tuple[dict[str, Any], list[str]]:
    parsed: dict[str, Any] = {}
    flags: list[str] = []
    normalized_compact = re.sub(r"\s*([:=])\s*", r"\1", raw_compact.strip())
    tokens = [
        token.strip()
        for token in re.split(r"[,;\s]+", normalized_compact)
        if token.strip()
    ]
    if not tokens:
        return parsed, flags

    for token in tokens:
        match = _KV_RE.match(token)
        if not match:
            flags.append(f"compact_token_ignored:{token}")
            continue
        canonical = _canonicalize_compact_key(match.group(1))
        if canonical is None:
            flags.append(f"compact_key_unknown:{match.group(1).strip().lower()}")
            continue
        parsed[canonical] = match.group(2).strip()
    return parsed, flags


def _parse_compact_pairs(raw_compact: str) -> tuple[dict[str, Any], list[str]]:
    parsed: dict[str, Any] = {}
    flags: list[str] = []
    tokens = [token.strip() for token in raw_compact.split() if token.strip()]
    if len(tokens) < 2:
        return parsed, flags

    idx = 0
    while idx + 1 < len(tokens):
        raw_key = tokens[idx]
        canonical = _canonicalize_compact_key(raw_key)
        if canonical is None:
            flags.append(f"compact_key_unknown:{raw_key.lower()}")
            idx += 1
            continue
        parsed[canonical] = tokens[idx + 1]
        idx += 2
    if idx < len(tokens):
        flags.append(f"compact_token_ignored:{tokens[idx]}")
    return parsed, flags


def _parse_compact_positional(raw_compact: str) -> tuple[dict[str, Any], list[str]]:
    parsed: dict[str, Any] = {}
    flags: list[str] = []
    tokens = [token.strip() for token in raw_compact.split(",")]
    tokens = [token for token in tokens if token]
    for idx, token in enumerate(tokens):
        if idx >= len(_POSITIONAL_FIELDS):
            flags.append(f"compact_extra_token_ignored:{token}")
            continue
        parsed[_POSITIONAL_FIELDS[idx]] = token
    return parsed, flags


def _parse_compact_input(raw_compact: str) -> tuple[dict[str, Any], str, list[str]]:
    compact = raw_compact.strip()
    if not compact:
        return {}, "none", []

    if "=" in compact or ":" in compact:
        parsed, flags = _parse_compact_key_value(compact)
        return parsed, "key_value", flags

    if "," in compact:
        parsed, flags = _parse_compact_positional(compact)
        return parsed, "positional", flags

    parsed, flags = _parse_compact_pairs(compact)
    if parsed:
        return parsed, "pair_tokens", flags
    return {}, "none", flags


def normalize_daily_checkin_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize recovery.daily_checkin payload into canonical typed fields."""
    raw = _normalized_key_map(payload)
    normalized: dict[str, Any] = {
        "parsed_from_compact": False,
        "compact_input_mode": "none",
        "quality_flags": [],
    }

    compact_value = _first_raw_value(raw, "compact_input")
    compact_fields: dict[str, Any] = {}
    if isinstance(compact_value, str) and compact_value.strip():
        compact_fields, mode, flags = _parse_compact_input(compact_value)
        normalized["parsed_from_compact"] = bool(compact_fields)
        normalized["compact_input_mode"] = mode
        normalized["quality_flags"].extend(flags)
    elif compact_value is not None:
        normalized["quality_flags"].append("compact_input_invalid_type")

    def _raw_value(field: str) -> Any:
        explicit = _first_raw_value(raw, field)
        if explicit is not None:
            return explicit
        return compact_fields.get(field)

    for field in _NUMERIC_FIELDS:
        parsed = _parse_decimal(_raw_value(field))
        if parsed is not None:
            normalized[field] = round(parsed, 2)
        elif _raw_value(field) is not None:
            normalized["quality_flags"].append(f"invalid_{field}")

    bool_value = _parse_bool(_raw_value("traveling_yesterday"))
    if bool_value is not None:
        normalized["traveling_yesterday"] = bool_value
    elif _raw_value("traveling_yesterday") is not None:
        normalized["quality_flags"].append("invalid_traveling_yesterday")

    bool_value = _parse_bool(_raw_value("sick_today"))
    if bool_value is not None:
        normalized["sick_today"] = bool_value
    elif _raw_value("sick_today") is not None:
        normalized["quality_flags"].append("invalid_sick_today")

    alcohol = _parse_alcohol(_raw_value("alcohol_last_night"))
    if alcohol is not None:
        normalized["alcohol_last_night"] = alcohol
    elif _raw_value("alcohol_last_night") is not None:
        normalized["quality_flags"].append("invalid_alcohol_last_night")

    training = _parse_training_tag(_raw_value("training_yesterday"))
    if training is not None:
        normalized["training_yesterday"] = training
    elif _raw_value("training_yesterday") is not None:
        normalized["quality_flags"].append("invalid_training_yesterday")

    notes = _to_text(_raw_value("notes"))
    if notes is not None:
        normalized["notes"] = notes

    supplements = _normalize_supplements(_raw_value("supplements"))
    if supplements is not None:
        normalized["supplements"] = supplements

    normalized["quality_flags"] = sorted(set(normalized["quality_flags"]))
    return normalized
