"""Tests for registry dimension metadata (Decision 7)."""

import pytest

from kura_workers.registry import (
    _dimension_metadata,
    _projection_handlers,
    get_dimension_metadata,
    projection_handler,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Remove test-registered handlers/metadata after each test."""
    snapshot_handlers = {k: list(v) for k, v in _projection_handlers.items()}
    snapshot_meta = dict(_dimension_metadata)
    yield
    _projection_handlers.clear()
    _projection_handlers.update(snapshot_handlers)
    _dimension_metadata.clear()
    _dimension_metadata.update(snapshot_meta)


class TestDimensionMeta:
    def test_stores_metadata(self):
        @projection_handler("test.event", dimension_meta={
            "name": "test_dim",
            "description": "A test dimension",
        })
        async def _handler(conn, payload):
            pass

        meta = get_dimension_metadata()
        assert "test_dim" in meta
        assert meta["test_dim"]["description"] == "A test dimension"

    def test_auto_captures_event_types(self):
        @projection_handler("foo.bar", "baz.qux", dimension_meta={
            "name": "test_multi_event",
        })
        async def _handler(conn, payload):
            pass

        meta = get_dimension_metadata()
        assert meta["test_multi_event"]["event_types"] == ["foo.bar", "baz.qux"]

    def test_duplicate_name_raises(self):
        @projection_handler("a.b", dimension_meta={"name": "dup_test"})
        async def _handler1(conn, payload):
            pass

        with pytest.raises(ValueError, match="Duplicate dimension_meta name='dup_test'"):
            @projection_handler("c.d", dimension_meta={"name": "dup_test"})
            async def _handler2(conn, payload):
                pass

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="dimension_meta must include 'name'"):
            @projection_handler("a.b", dimension_meta={"description": "no name"})
            async def _handler(conn, payload):
                pass

    def test_without_dimension_meta_still_works(self):
        @projection_handler("plain.event")
        async def _handler(conn, payload):
            pass

        # Handler registered for event dispatch
        assert _handler in _projection_handlers.get("plain.event", [])
        # But no dimension metadata
        meta = get_dimension_metadata()
        assert "plain" not in meta

    def test_preserves_all_fields(self):
        contrib_fn = lambda rows: {"x": 1}

        @projection_handler("e.f", dimension_meta={
            "name": "rich_dim",
            "description": "Rich",
            "key_structure": "one per thing",
            "granularity": ["day", "week"],
            "relates_to": {"other": {"join": "week", "why": "testing"}},
            "manifest_contribution": contrib_fn,
        })
        async def _handler(conn, payload):
            pass

        meta = get_dimension_metadata()["rich_dim"]
        assert meta["key_structure"] == "one per thing"
        assert meta["granularity"] == ["day", "week"]
        assert meta["relates_to"] == {"other": {"join": "week", "why": "testing"}}
        assert meta["manifest_contribution"] is contrib_fn
        assert meta["event_types"] == ["e.f"]

    def test_get_dimension_metadata_returns_copy(self):
        @projection_handler("g.h", dimension_meta={"name": "copy_test"})
        async def _handler(conn, payload):
            pass

        meta1 = get_dimension_metadata()
        meta2 = get_dimension_metadata()
        assert meta1 is not meta2
        assert meta1 == meta2
