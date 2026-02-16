from datetime import timezone

import pytest

from kura_workers.handlers.account_lifecycle import (
    _parse_requested_at,
    handle_account_hard_delete,
)


def test_parse_requested_at_accepts_rfc3339() -> None:
    parsed = _parse_requested_at("2026-02-16T10:15:30Z")
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.year == 2026
    assert parsed.minute == 15


def test_parse_requested_at_rejects_invalid_values() -> None:
    assert _parse_requested_at(None) is None
    assert _parse_requested_at("") is None
    assert _parse_requested_at("not-a-timestamp") is None


@pytest.mark.asyncio
async def test_handle_account_hard_delete_requires_user_id() -> None:
    with pytest.raises(ValueError, match="Missing user_id"):
        await handle_account_hard_delete(None, {})


@pytest.mark.asyncio
async def test_handle_account_hard_delete_rejects_invalid_uuid() -> None:
    with pytest.raises(ValueError, match="Invalid user_id"):
        await handle_account_hard_delete(None, {"user_id": "not-a-uuid"})
