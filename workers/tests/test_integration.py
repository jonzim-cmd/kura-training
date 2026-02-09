"""Integration tests: handlers against real PostgreSQL.

Requires: PostgreSQL running with migrations applied.
Run: DATABASE_URL=postgresql://kura:kura_dev_password@localhost:5432/kura uv run pytest tests/test_integration.py -v

Each test:
1. Creates a test user + events (as kura user)
2. Switches to app_worker role (matches production)
3. Calls the handler directly
4. Verifies projection output
5. Transaction rolls back automatically (fixture)
"""

import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

# Import handlers to trigger registration
import kura_workers.handlers  # noqa: F401
from kura_workers.handlers.body_composition import update_body_composition
from kura_workers.handlers.exercise_progression import update_exercise_progression
from kura_workers.handlers.nutrition import update_nutrition
from kura_workers.handlers.recovery import update_recovery
from kura_workers.handlers.router import handle_projection_retry, handle_projection_update
from kura_workers.handlers.training_plan import update_training_plan
from kura_workers.handlers.training_timeline import update_training_timeline
from kura_workers.handlers.user_profile import update_user_profile

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


# --- Fixtures ---


@pytest.fixture
async def db():
    """Async DB connection with automatic rollback after each test."""
    conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.rollback()
        await conn.execute("RESET ROLE")
        await conn.close()


@pytest.fixture
def test_user_id():
    return str(uuid.uuid4())


# --- Helpers ---


async def create_test_user(conn, user_id: str) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, display_name) VALUES (%s, %s, 'h', 'Test')",
        (user_id, f"test-{user_id[:8]}@test.local"),
    )


async def insert_event(conn, user_id, event_type, data, timestamp="NOW()"):
    event_id = str(uuid.uuid4())
    metadata = {"idempotency_key": str(uuid.uuid4())}
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            INSERT INTO events (id, user_id, event_type, data, metadata, timestamp)
            VALUES (%s, %s, %s, %s, %s, {timestamp})
            RETURNING id
            """,
            (
                event_id, user_id, event_type,
                psycopg.types.json.Json(data),
                psycopg.types.json.Json(metadata),
            ),
        )
        row = await cur.fetchone()
        return str(row["id"])


async def get_projection(conn, user_id, projection_type, key="overview"):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT * FROM projections WHERE user_id = %s AND projection_type = %s AND key = %s",
            (user_id, projection_type, key),
        )
        return await cur.fetchone()


# ---------------------------------------------------------------------------
# Body Composition
# ---------------------------------------------------------------------------


class TestBodyCompositionIntegration:
    async def test_bodyweight_events_create_projection(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 82.5, "time_of_day": "morning",
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 82.0,
        }, "TIMESTAMP '2026-02-08 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj is not None
        data = proj["data"]
        assert data["current_weight_kg"] == 82.0
        assert data["total_weigh_ins"] == 2
        assert len(data["weight_trend"]["recent_entries"]) == 2
        assert data["weight_trend"]["all_time"]["min_kg"] == 82.0
        assert data["weight_trend"]["all_time"]["max_kg"] == 82.5

    async def test_weight_target_in_projection(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 85.0,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "weight_target.set", {
            "weight_kg": 80, "timeframe": "3 months",
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "weight_target.set",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        data = proj["data"]
        assert data["current_weight_kg"] == 85.0
        assert data["target"]["weight_kg"] == 80

    async def test_measurement_events(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "measurement.logged", {
            "type": "Waist", "value_cm": 85.0,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "measurement.logged", {
            "type": "waist", "value_cm": 84.0,
        }, "TIMESTAMP '2026-02-08 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "measurement.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        data = proj["data"]
        assert "waist" in data["measurements"]
        assert data["measurements"]["waist"]["current_cm"] == 84.0
        assert data["measurement_types"] == ["waist"]


# ---------------------------------------------------------------------------
# Exercise Progression (needs event_id in payload)
# ---------------------------------------------------------------------------


class TestExerciseProgressionIntegration:
    async def test_basic_sets(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise": "Bench Press", "exercise_id": "bench_press",
            "weight_kg": 80, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        event_id = await insert_event(db, test_user_id, "set.logged", {
            "exercise": "Bench Press", "exercise_id": "bench_press",
            "weight_kg": 85, "reps": 3,
        }, "TIMESTAMP '2026-02-01 10:05:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "set.logged",
            "event_id": event_id,
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "exercise_progression", "bench_press")
        assert proj is not None
        data = proj["data"]
        assert data["total_sets"] == 2
        assert data["estimated_1rm"] > 0
        assert data["total_volume_kg"] > 0

    async def test_alias_consolidation(self, db, test_user_id):
        """Alias events should merge projections under the canonical name."""
        await create_test_user(db, test_user_id)
        # First: log under the raw user term
        event_1 = await insert_event(db, test_user_id, "set.logged", {
            "exercise": "Kniebeuge", "weight_kg": 100, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        # Build initial projection under raw key "kniebeuge"
        await db.execute("SET ROLE app_worker")
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "set.logged",
            "event_id": event_1,
        })

        # Verify old projection exists under "kniebeuge"
        old = await get_projection(db, test_user_id, "exercise_progression", "kniebeuge")
        assert old is not None
        await db.execute("RESET ROLE")

        # Then: create alias mapping
        alias_event = await insert_event(db, test_user_id, "exercise.alias_created", {
            "alias": "kniebeuge", "exercise_id": "barbell_back_squat",
            "confidence": "confirmed",
        }, "TIMESTAMP '2026-02-01 10:01:00+01'")
        # Log under canonical name too
        await insert_event(db, test_user_id, "set.logged", {
            "exercise": "Back Squat", "exercise_id": "barbell_back_squat",
            "weight_kg": 110, "reps": 3,
        }, "TIMESTAMP '2026-02-02 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "exercise.alias_created",
            "event_id": alias_event,
        })
        await db.execute("RESET ROLE")

        # Canonical projection should have both sets merged
        proj = await get_projection(
            db, test_user_id, "exercise_progression", "barbell_back_squat"
        )
        assert proj is not None
        data = proj["data"]
        assert data["total_sets"] == 2

        # Old alias-named projection should be deleted
        old = await get_projection(db, test_user_id, "exercise_progression", "kniebeuge")
        assert old is None


# ---------------------------------------------------------------------------
# Training Timeline
# ---------------------------------------------------------------------------


class TestTrainingTimelineIntegration:
    async def test_training_days_and_streak(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        for day in range(1, 8):
            if day % 2 == 0:  # train every other day: Feb 2, 4, 6
                await insert_event(db, test_user_id, "set.logged", {
                    "exercise_id": "squat", "weight_kg": 100, "reps": 5,
                }, f"TIMESTAMP '2026-02-{day:02d} 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_training_timeline(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "training_timeline")
        assert proj is not None
        data = proj["data"]
        assert data["total_training_days"] == 3
        assert len(data["recent_days"]) == 3


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


class TestRecoveryIntegration:
    async def test_sleep_and_soreness(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 7.5, "quality": 4,
        }, "TIMESTAMP '2026-02-01 07:00:00+01'")
        await insert_event(db, test_user_id, "soreness.logged", {
            "area": "lower_back", "severity": 3,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "energy.logged", {
            "level": 7,
        }, "TIMESTAMP '2026-02-01 09:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_recovery(db, {
            "user_id": test_user_id, "event_type": "sleep.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "recovery")
        assert proj is not None
        data = proj["data"]
        assert data["sleep"]["overall"]["avg_duration_hours"] == 7.5
        assert data["soreness"]["total_entries"] == 1
        areas = [e["area"] for e in data["soreness"]["current"]]
        assert "lower_back" in areas
        assert data["energy"]["overall"]["avg_level"] == 7.0

    async def test_sleep_target(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 6.5,
        }, "TIMESTAMP '2026-02-01 07:00:00+01'")
        await insert_event(db, test_user_id, "sleep_target.set", {
            "duration_hours": 8, "bedtime": "22:30",
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_recovery(db, {
            "user_id": test_user_id, "event_type": "sleep_target.set",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "recovery")
        data = proj["data"]
        assert data["targets"]["sleep"]["duration_hours"] == 8


# ---------------------------------------------------------------------------
# Nutrition
# ---------------------------------------------------------------------------


class TestNutritionIntegration:
    async def test_meal_aggregation(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "meal.logged", {
            "calories": 600, "protein_g": 40, "carbs_g": 70, "fat_g": 20,
            "meal_type": "breakfast",
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "meal.logged", {
            "calories": 800, "protein_g": 50, "carbs_g": 90, "fat_g": 25,
            "meal_type": "lunch",
        }, "TIMESTAMP '2026-02-01 12:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_nutrition(db, {
            "user_id": test_user_id, "event_type": "meal.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "nutrition")
        assert proj is not None
        data = proj["data"]
        assert data["total_meals"] == 2
        assert data["tracking_days"] == 1
        assert len(data["daily_totals"]) == 1
        day = data["daily_totals"][0]
        assert day["calories"] == 1400
        assert day["protein_g"] == 90

    async def test_nutrition_target(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "meal.logged", {
            "calories": 500, "protein_g": 30,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "nutrition_target.set", {
            "calories": 2500, "protein_g": 180,
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_nutrition(db, {
            "user_id": test_user_id, "event_type": "nutrition_target.set",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "nutrition")
        data = proj["data"]
        assert data["target"]["calories"] == 2500
        assert data["target"]["protein_g"] == 180


# ---------------------------------------------------------------------------
# Training Plan
# ---------------------------------------------------------------------------


class TestTrainingPlanIntegration:
    async def test_plan_lifecycle(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "training_plan.created", {
            "plan_id": "plan-001",
            "name": "Hypertrophy Block",
            "sessions": [
                {"day": "monday", "name": "Upper A", "exercises": ["bench_press", "rows"]},
                {"day": "thursday", "name": "Lower A", "exercises": ["squat", "rdl"]},
            ],
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_training_plan(db, {
            "user_id": test_user_id, "event_type": "training_plan.created",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "training_plan")
        data = proj["data"]
        assert data["active_plan"]["name"] == "Hypertrophy Block"
        assert len(data["active_plan"]["sessions"]) == 2

    async def test_plan_archive(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "training_plan.created", {
            "plan_id": "plan-001", "name": "Old Plan", "sessions": [],
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "training_plan.archived", {
            "plan_id": "plan-001",
        }, "TIMESTAMP '2026-02-08 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_training_plan(db, {
            "user_id": test_user_id, "event_type": "training_plan.archived",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "training_plan")
        data = proj["data"]
        assert data["active_plan"] is None
        assert len(data["plan_history"]) == 1


# ---------------------------------------------------------------------------
# User Profile (Three-Layer Entry Point)
# ---------------------------------------------------------------------------


class TestUserProfileIntegration:
    async def test_three_layer_output(self, db, test_user_id):
        """Full profile build with events across multiple dimensions."""
        await create_test_user(db, test_user_id)

        # user_profile needs identity events (set.logged, profile.updated, etc.)
        set_event = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 80, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 82.0,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "profile.updated", {
            "training_experience": "3 years",
        }, "TIMESTAMP '2026-02-01 09:00:00+01'")

        await db.execute("SET ROLE app_worker")

        # First build dimension projections (exercise_progression needs event_id)
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "set.logged",
            "event_id": set_event,
        })
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        # Then build user_profile (reads other projections)
        await update_user_profile(db, {
            "user_id": test_user_id, "event_type": "profile.updated",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "user_profile", "me")
        assert proj is not None
        data = proj["data"]

        # Three-layer structure
        assert "system" in data
        assert "user" in data
        assert "agenda" in data

        # System layer
        assert "dimensions" in data["system"]
        assert "conventions" in data["system"]
        assert "interview_guide" in data["system"]

        # User layer
        assert "profile" in data["user"]
        assert "dimensions" in data["user"]
        assert "data_quality" in data["user"]
        assert "interview_coverage" in data["user"]

        # Exercises should be tracked
        assert "bench_press" in data["user"]["exercises_logged"]

    async def test_orphaned_event_types_detected(self, db, test_user_id):
        """Unknown event types should appear in data_quality.

        user_profile only queries specific known event types internally.
        To reach the orphaned detection code, the user must also have
        at least one known event type.
        """
        await create_test_user(db, test_user_id)
        # A known event so user_profile doesn't early-return
        await insert_event(db, test_user_id, "profile.updated", {
            "name": "Test",
        }, "TIMESTAMP '2026-02-01 07:00:00+01'")
        # An unknown event
        await insert_event(db, test_user_id, "totally.unknown.event", {
            "some": "data",
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_user_profile(db, {
            "user_id": test_user_id, "event_type": "totally.unknown.event",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "user_profile", "me")
        assert proj is not None
        data = proj["data"]
        data_quality = data["user"]["data_quality"]
        assert "orphaned_event_types" in data_quality
        orphans = data_quality["orphaned_event_types"]
        assert any(o["event_type"] == "totally.unknown.event" for o in orphans)


# ---------------------------------------------------------------------------
# Router: Multi-Handler Dispatch
# ---------------------------------------------------------------------------


class TestRouterIntegration:
    async def test_router_dispatches_to_dimension_and_profile(self, db, test_user_id):
        """Router dispatches set.logged to exercise_progression + training_timeline + user_profile."""
        await create_test_user(db, test_user_id)
        event_id = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "squat", "weight_kg": 100, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await handle_projection_update(db, {
            "event_id": event_id,
            "event_type": "set.logged",
            "user_id": test_user_id,
        })
        await db.execute("RESET ROLE")

        # exercise_progression should exist
        ep = await get_projection(db, test_user_id, "exercise_progression", "squat")
        assert ep is not None
        assert ep["data"]["total_sets"] == 1

        # training_timeline should exist
        tt = await get_projection(db, test_user_id, "training_timeline")
        assert tt is not None
        assert tt["data"]["total_training_days"] == 1

        # user_profile should also exist (set.logged is in its event list)
        up = await get_projection(db, test_user_id, "user_profile", "me")
        assert up is not None
        assert "system" in up["data"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    async def test_handler_produces_same_result_on_double_call(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 82.5,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")

        payload = {"user_id": test_user_id, "event_type": "bodyweight.logged"}
        await update_body_composition(db, payload)
        proj1 = await get_projection(db, test_user_id, "body_composition")

        await update_body_composition(db, payload)
        proj2 = await get_projection(db, test_user_id, "body_composition")

        await db.execute("RESET ROLE")

        assert proj1["data"] == proj2["data"]
        assert proj2["version"] == proj1["version"] + 1


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_event_with_missing_fields_skipped_gracefully(self, db, test_user_id):
        """Events with invalid data shouldn't crash the handler."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "notes": "forgot to add weight",
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 80.0,
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        data = proj["data"]
        assert data["total_weigh_ins"] == 1
        assert data["current_weight_kg"] == 80.0

    async def test_target_only_no_logged_events(self, db, test_user_id):
        """A target event without any logged events should still create a projection."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "weight_target.set", {
            "weight_kg": 75,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "weight_target.set",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj is not None
        data = proj["data"]
        assert data["current_weight_kg"] is None
        assert data["total_weigh_ins"] == 0
        assert data["target"]["weight_kg"] == 75

    async def test_no_events_for_user(self, db, test_user_id):
        """Handler with no events should not crash or create projection."""
        await create_test_user(db, test_user_id)

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj is None


# ---------------------------------------------------------------------------
# Projection Retry
# ---------------------------------------------------------------------------


class TestProjectionRetryIntegration:
    async def test_retry_calls_handler_successfully(self, db, test_user_id):
        """projection.retry should call the named handler and produce a projection."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 80.0,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await handle_projection_retry(db, {
            "handler_name": "update_body_composition",
            "event_type": "bodyweight.logged",
            "user_id": test_user_id,
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj is not None
        assert proj["data"]["current_weight_kg"] == 80.0

    async def test_router_failure_creates_retry_job(self, db, test_user_id):
        """When a handler fails during routing, a retry job should be created."""
        await create_test_user(db, test_user_id)
        event_id = await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 80.0,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")

        # Monkeypatch body_composition handler to fail
        original_handler = update_body_composition.__wrapped__ if hasattr(update_body_composition, '__wrapped__') else None

        from unittest.mock import patch
        from kura_workers.registry import get_projection_handlers

        # Get the actual handlers for bodyweight.logged
        handlers = get_projection_handlers("bodyweight.logged")
        # Replace the first handler (body_composition) with one that fails
        failing_handler = handlers[0]
        original_fn = failing_handler

        async def always_fail(conn, payload):
            raise RuntimeError("Simulated handler failure")
        always_fail.__name__ = original_fn.__name__

        # Patch at registry level
        from kura_workers.registry import _projection_handlers
        old_handlers = _projection_handlers["bodyweight.logged"]
        _projection_handlers["bodyweight.logged"] = [always_fail] + old_handlers[1:]

        try:
            await handle_projection_update(db, {
                "event_type": "bodyweight.logged",
                "user_id": test_user_id,
                "event_id": event_id,
            })
        finally:
            _projection_handlers["bodyweight.logged"] = old_handlers

        await db.execute("RESET ROLE")

        # Check that a retry job was created
        async with db.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM background_jobs WHERE user_id = %s AND job_type = 'projection.retry'",
                (test_user_id,),
            )
            retry_jobs = await cur.fetchall()

        assert len(retry_jobs) == 1
        job = retry_jobs[0]
        assert job["payload"]["handler_name"] == original_fn.__name__
        assert job["payload"]["event_type"] == "bodyweight.logged"
        assert job["status"] == "pending"
