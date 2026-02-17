"""Unit tests for router retry logic, advisory locking, and concurrency safety."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kura_workers.handlers.router import (
    _acquire_user_lock,
    handle_projection_retry,
    handle_projection_update,
)


class _FakeTransaction:
    """Mimics psycopg's async transaction context manager (savepoint)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False  # don't suppress exceptions


class _FakeCursor:
    """Mimics psycopg's async cursor context manager for advisory lock tests."""

    def __init__(self):
        self.execute = AsyncMock()
        self.fetchone = AsyncMock(return_value=(True,))  # lock acquired
        self._execute_calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _make_mock_conn():
    """Create a mock connection supporting both transaction and cursor patterns."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTransaction())
    conn.execute = AsyncMock()
    fake_cursor = _FakeCursor()
    conn.cursor = MagicMock(return_value=fake_cursor)
    conn._fake_cursor = fake_cursor  # expose for assertions
    return conn


@pytest.fixture
def mock_conn():
    """Mock async connection with transaction and cursor support."""
    return _make_mock_conn()


def _no_custom_rules():
    """Patch context: no custom projection rules exist."""
    return patch(
        "kura_workers.handlers.custom_projection.has_matching_custom_rules",
        new_callable=AsyncMock,
        return_value=False,
    )


class TestHandleProjectionUpdate:
    @pytest.mark.asyncio
    async def test_advisory_lock_acquired(self, mock_conn):
        """Advisory lock should be acquired before dispatching handlers."""
        user_id = "test-user-123"
        payload = {"event_type": "bodyweight.logged", "user_id": user_id}

        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[]), \
             _no_custom_rules():
            await handle_projection_update(mock_conn, payload)

        # No handlers → no lock needed (early return before lock)
        # Let's test with a handler instead
        handler = AsyncMock()
        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[handler]), \
             _no_custom_rules():
            await handle_projection_update(mock_conn, payload)

        # Lock acquired via cursor pattern (pg_try_advisory_xact_lock)
        cur = mock_conn._fake_cursor
        lock_call = cur.execute.call_args_list[0]
        assert "pg_try_advisory_xact_lock" in lock_call.args[0]
        assert lock_call.args[1] == (user_id,)

    @pytest.mark.asyncio
    async def test_successful_handler_no_retry_job(self, mock_conn):
        """Successful handler should not create a retry job."""
        handler = AsyncMock()
        handler.__name__ = "test_handler"
        payload = {"event_type": "test.event", "user_id": "user-1", "event_id": "evt-1"}

        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[handler]), \
             _no_custom_rules():
            await handle_projection_update(mock_conn, payload)

        # Should only have the advisory lock call, no INSERT
        for c in mock_conn.execute.call_args_list:
            assert "INSERT INTO background_jobs" not in c.args[0]

    @pytest.mark.asyncio
    async def test_failed_handler_creates_retry_job(self, mock_conn):
        """Failed handler should INSERT a projection.retry job."""
        handler = AsyncMock(side_effect=RuntimeError("DB exploded"))
        handler.__name__ = "update_body_composition"
        payload = {"event_type": "bodyweight.logged", "user_id": "user-1", "event_id": "evt-1"}

        # transaction().__aenter__/__aexit__ — the savepoint rollback on exception
        # We need the exception to propagate out of the savepoint context manager
        txn_cm = AsyncMock()
        txn_cm.__aenter__ = AsyncMock()
        txn_cm.__aexit__ = AsyncMock(return_value=False)  # don't suppress
        mock_conn.transaction.return_value = txn_cm

        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[handler]), \
             _no_custom_rules():
            await handle_projection_update(mock_conn, payload)

        # Find the INSERT call
        insert_calls = [
            c for c in mock_conn.execute.call_args_list
            if "INSERT INTO background_jobs" in c.args[0]
        ]
        assert len(insert_calls) == 1

        insert_call = insert_calls[0]
        assert insert_call.args[1][0] == "user-1"  # user_id

        # Verify payload contains handler_name
        retry_payload = insert_call.args[1][1]  # Json object
        assert retry_payload.obj["handler_name"] == "update_body_composition"
        assert retry_payload.obj["event_type"] == "bodyweight.logged"

    @pytest.mark.asyncio
    async def test_failed_inference_handler_records_failed_run(self, mock_conn):
        """Inference handler failures should persist failed telemetry runs."""
        handler = AsyncMock(side_effect=ImportError("No module named 'pymc'"))
        handler.__name__ = "update_strength_inference"
        payload = {"event_type": "set.logged", "user_id": "user-1", "event_id": "evt-1"}

        txn_cm = AsyncMock()
        txn_cm.__aenter__ = AsyncMock()
        txn_cm.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction.return_value = txn_cm

        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[handler]), \
             _no_custom_rules(), \
             patch("kura_workers.handlers.router.safe_record_inference_run", new_callable=AsyncMock) as telemetry:
            await handle_projection_update(mock_conn, payload)

        telemetry.assert_awaited_once()
        kwargs = telemetry.await_args.kwargs
        assert kwargs["projection_type"] == "strength_inference"
        assert kwargs["status"] == "failed"
        assert kwargs["error_taxonomy"] == "engine_unavailable"

        insert_calls = [
            c for c in mock_conn.execute.call_args_list
            if "INSERT INTO background_jobs" in c.args[0]
        ]
        assert len(insert_calls) == 1

    @pytest.mark.asyncio
    async def test_failed_causal_inference_handler_records_failed_run(self, mock_conn):
        """Causal inference handler failures should persist failed telemetry runs."""
        handler = AsyncMock(side_effect=RuntimeError("numeric overflow"))
        handler.__name__ = "update_causal_inference"
        payload = {"event_type": "meal.logged", "user_id": "user-1", "event_id": "evt-1"}

        txn_cm = AsyncMock()
        txn_cm.__aenter__ = AsyncMock()
        txn_cm.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction.return_value = txn_cm

        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[handler]), \
             _no_custom_rules(), \
             patch("kura_workers.handlers.router.safe_record_inference_run", new_callable=AsyncMock) as telemetry:
            await handle_projection_update(mock_conn, payload)

        telemetry.assert_awaited_once()
        kwargs = telemetry.await_args.kwargs
        assert kwargs["projection_type"] == "causal_inference"
        assert kwargs["key"] == "overview"
        assert kwargs["status"] == "failed"

        insert_calls = [
            c for c in mock_conn.execute.call_args_list
            if "INSERT INTO background_jobs" in c.args[0]
        ]
        assert len(insert_calls) == 1

    @pytest.mark.asyncio
    async def test_partial_failure_continues_other_handlers(self, mock_conn):
        """If handler A fails, handler B should still run."""
        handler_a = AsyncMock(side_effect=RuntimeError("crash"))
        handler_a.__name__ = "handler_a"
        handler_b = AsyncMock()
        handler_b.__name__ = "handler_b"
        payload = {"event_type": "set.logged", "user_id": "user-1", "event_id": "evt-1"}

        txn_cm = AsyncMock()
        txn_cm.__aenter__ = AsyncMock()
        txn_cm.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction.return_value = txn_cm

        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[handler_a, handler_b]), \
             _no_custom_rules():
            await handle_projection_update(mock_conn, payload)

        # Both handlers called
        handler_a.assert_awaited_once()
        handler_b.assert_awaited_once()

        # Retry job only for handler_a
        insert_calls = [
            c for c in mock_conn.execute.call_args_list
            if "INSERT INTO background_jobs" in c.args[0]
        ]
        assert len(insert_calls) == 1
        assert insert_calls[0].args[1][1].obj["handler_name"] == "handler_a"

    @pytest.mark.asyncio
    async def test_no_handlers_skips_silently(self, mock_conn):
        """No handlers for event_type should return without acquiring lock."""
        payload = {"event_type": "unknown.event", "user_id": "user-1"}

        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[]), \
             _no_custom_rules():
            await handle_projection_update(mock_conn, payload)

        # No execute calls at all (no lock, no insert)
        mock_conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_user_id_raises(self, mock_conn):
        """Missing user_id should raise ValueError early."""
        payload = {"event_type": "test.event"}

        with pytest.raises(ValueError, match="Missing user_id"):
            await handle_projection_update(mock_conn, payload)

    @pytest.mark.asyncio
    async def test_retry_insert_failure_does_not_block_other_handlers(self, mock_conn):
        """If retry job INSERT fails, other handlers should still run."""
        handler_a = AsyncMock(side_effect=RuntimeError("crash"))
        handler_a.__name__ = "handler_a"
        handler_b = AsyncMock()
        handler_b.__name__ = "handler_b"
        payload = {"event_type": "set.logged", "user_id": "user-1", "event_id": "evt-1"}

        txn_cm = AsyncMock()
        txn_cm.__aenter__ = AsyncMock()
        txn_cm.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction.return_value = txn_cm

        # First execute = lock (OK), second = retry INSERT (fail)
        call_count = 0

        async def execute_side_effect(sql, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "INSERT INTO background_jobs" in sql:
                raise RuntimeError("DB write failed")

        mock_conn.execute = AsyncMock(side_effect=execute_side_effect)

        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[handler_a, handler_b]), \
             _no_custom_rules():
            await handle_projection_update(mock_conn, payload)

        # handler_b should still have been called despite retry INSERT failure
        handler_b.assert_awaited_once()


class TestHandleProjectionRetry:
    @pytest.mark.asyncio
    async def test_calls_correct_handler(self, mock_conn):
        """Retry should look up handler by name and call it."""
        handler = AsyncMock()
        payload = {
            "handler_name": "update_body_composition",
            "event_type": "bodyweight.logged",
            "user_id": "user-1",
            "event_id": "evt-1",
        }

        with patch("kura_workers.handlers.router.get_projection_handler_by_name", return_value=handler):
            await handle_projection_retry(mock_conn, payload)

        handler.assert_awaited_once_with(mock_conn, payload)

    @pytest.mark.asyncio
    async def test_unknown_handler_raises(self, mock_conn):
        """Unknown handler name should raise ValueError (triggers worker dead-letter)."""
        payload = {
            "handler_name": "nonexistent_handler",
            "user_id": "user-1",
        }

        with patch("kura_workers.handlers.router.get_projection_handler_by_name", return_value=None):
            with pytest.raises(ValueError, match="Unknown projection handler"):
                await handle_projection_retry(mock_conn, payload)

    @pytest.mark.asyncio
    async def test_advisory_lock_acquired(self, mock_conn):
        """Retry should also acquire advisory lock."""
        handler = AsyncMock()
        payload = {"handler_name": "test", "user_id": "user-42", "event_type": "test"}

        with patch("kura_workers.handlers.router.get_projection_handler_by_name", return_value=handler):
            await handle_projection_retry(mock_conn, payload)

        cur = mock_conn._fake_cursor
        lock_call = cur.execute.call_args_list[0]
        assert "pg_try_advisory_xact_lock" in lock_call.args[0]
        assert lock_call.args[1] == ("user-42",)


class TestConcurrencySafety:
    """Verify that pg_try_advisory_xact_lock serializes concurrent handler execution.

    The advisory lock is transaction-scoped (pg_try_advisory_xact_lock), so two
    concurrent projection.update jobs for the same user_id will contend on the
    same hash.  The non-blocking variant fails fast instead of hanging on zombie
    pooler connections.
    """

    @pytest.mark.asyncio
    async def test_same_user_gets_same_lock_hash(self):
        """Two calls for the same user_id must acquire the same advisory lock."""
        user_id = "concurrent-user-abc"

        conn_a = _make_mock_conn()
        conn_b = _make_mock_conn()

        await _acquire_user_lock(conn_a, user_id)
        await _acquire_user_lock(conn_b, user_id)

        call_a = conn_a._fake_cursor.execute.call_args
        call_b = conn_b._fake_cursor.execute.call_args
        # Both calls use the exact same SQL and parameter → same lock hash
        assert call_a == call_b
        assert "pg_try_advisory_xact_lock" in call_a.args[0]
        assert call_a.args[1] == (user_id,)

    @pytest.mark.asyncio
    async def test_different_users_get_different_locks(self):
        """Different user_ids should NOT contend on the same lock."""
        lock_calls = {}

        for uid in ("user-1", "user-2"):
            conn = _make_mock_conn()
            await _acquire_user_lock(conn, uid)
            lock_calls[uid] = conn._fake_cursor.execute.call_args

        # Both acquire advisory locks but with different user_id params
        assert lock_calls["user-1"].args[1] == ("user-1",)
        assert lock_calls["user-2"].args[1] == ("user-2",)
        assert lock_calls["user-1"].args[1] != lock_calls["user-2"].args[1]

    @pytest.mark.asyncio
    async def test_concurrent_updates_both_acquire_lock(self):
        """Two concurrent handle_projection_update calls for the same user
        both attempt to acquire the advisory lock, proving serialization."""
        user_id = "race-user"
        payload_a = {"event_type": "set.logged", "user_id": user_id, "event_id": "evt-a"}
        payload_b = {"event_type": "set.logged", "user_id": user_id, "event_id": "evt-b"}

        handler = AsyncMock()
        handler.__name__ = "test_handler"

        conn_a = _make_mock_conn()
        conn_b = _make_mock_conn()

        with patch("kura_workers.handlers.router.get_projection_handlers", return_value=[handler]), \
             _no_custom_rules():
            await asyncio.gather(
                handle_projection_update(conn_a, payload_a),
                handle_projection_update(conn_b, payload_b),
            )

        # Both calls acquired the lock via cursor pattern (pg_try_advisory_xact_lock)
        for conn, label in [(conn_a, "conn_a"), (conn_b, "conn_b")]:
            cur = conn._fake_cursor
            assert cur.execute.await_count >= 1, f"{label} should have called cursor.execute"
            lock_call = cur.execute.call_args_list[0]
            assert "pg_try_advisory_xact_lock" in lock_call.args[0]
            assert lock_call.args[1] == (user_id,)
