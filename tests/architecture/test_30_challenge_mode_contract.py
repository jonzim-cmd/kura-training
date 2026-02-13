from __future__ import annotations

from kura_workers.event_conventions import get_event_conventions
from kura_workers.interview_guide import get_interview_guide


def test_challenge_mode_is_chat_discoverable_in_interview_primer() -> None:
    guide = get_interview_guide()
    hint = guide["collaboration_primer"]["challenge_mode_hint"].lower()
    assert "auto" in hint
    assert "challenge mode aus" in hint


def test_preference_catalog_contains_challenge_mode_keys() -> None:
    conventions = get_event_conventions()
    keys = conventions["preference.set"]["common_keys"]
    assert "challenge_mode" in keys
    assert "challenge_mode_intro_seen" in keys
