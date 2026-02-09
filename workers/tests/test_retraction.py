"""Tests for retraction (event.retracted) support across all handlers.

Unit tests verify the retraction utility functions and structural contracts.
Integration tests (require DATABASE_URL) verify end-to-end retraction behavior.

Retraction design:
- event.retracted contains retracted_event_id (required) + retracted_event_type (recommended)
- Router resolves retraction → re-routes to handlers for the retracted event's type
- Every handler calls get_retracted_event_ids() and filters on every invocation
- When all events for a projection are retracted, the projection is DELETED (cleanup)
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
from kura_workers.handlers.router import handle_projection_update
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


async def retract_event(conn, user_id, retracted_event_id, retracted_event_type=None, timestamp="NOW()"):
    """Insert an event.retracted event."""
    data = {"retracted_event_id": retracted_event_id}
    if retracted_event_type:
        data["retracted_event_type"] = retracted_event_type
    return await insert_event(conn, user_id, "event.retracted", data, timestamp)


async def get_projection(conn, user_id, projection_type, key="overview"):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT * FROM projections WHERE user_id = %s AND projection_type = %s AND key = %s",
            (user_id, projection_type, key),
        )
        return await cur.fetchone()


# ---------------------------------------------------------------------------
# Body Composition Retraction
# ---------------------------------------------------------------------------


class TestBodyCompositionRetraction:
    async def test_retracted_event_excluded(self, db, test_user_id):
        """Retracted bodyweight event should be excluded from projection."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 82.5,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 83.0,
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")

        # Retract the first event
        await retract_event(db, test_user_id, ev1, "bodyweight.logged",
                            "TIMESTAMP '2026-02-03 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj is not None
        data = proj["data"]
        assert data["total_weigh_ins"] == 1
        assert data["current_weight_kg"] == 83.0

    async def test_all_events_retracted_deletes_projection(self, db, test_user_id):
        """When all bodyweight events are retracted, projection should be deleted."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 82.5,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        # Build projection first
        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj is not None
        await db.execute("RESET ROLE")

        # Now retract the only event
        await retract_event(db, test_user_id, ev1, "bodyweight.logged",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj is None

    async def test_retracted_target_excluded(self, db, test_user_id):
        """Retracted weight target should be excluded."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 85.0,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        target_ev = await insert_event(db, test_user_id, "weight_target.set", {
            "weight_kg": 80, "timeframe": "3 months",
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")
        await retract_event(db, test_user_id, target_ev, "weight_target.set",
                            "TIMESTAMP '2026-02-03 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        data = proj["data"]
        assert data["current_weight_kg"] == 85.0
        assert "target" not in data


# ---------------------------------------------------------------------------
# Exercise Progression Retraction
# ---------------------------------------------------------------------------


class TestExerciseProgressionRetraction:
    async def test_retracted_set_excluded(self, db, test_user_id):
        """Retracted set.logged should be excluded from exercise progression."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 80, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        ev2 = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 85, "reps": 3,
        }, "TIMESTAMP '2026-02-01 10:05:00+01'")

        # Retract the first set
        await retract_event(db, test_user_id, ev1, "set.logged",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "set.logged",
            "event_id": ev2,
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "exercise_progression", "bench_press")
        assert proj is not None
        data = proj["data"]
        assert data["total_sets"] == 1

    async def test_all_sets_retracted_deletes_projection(self, db, test_user_id):
        """When all sets for an exercise are retracted, projection should be deleted."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "squat", "weight_kg": 100, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        # Build projection
        await db.execute("SET ROLE app_worker")
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "set.logged",
            "event_id": ev1,
        })
        proj = await get_projection(db, test_user_id, "exercise_progression", "squat")
        assert proj is not None
        await db.execute("RESET ROLE")

        # Retract
        await retract_event(db, test_user_id, ev1, "set.logged",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "set.logged",
            "event_id": ev1,
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "exercise_progression", "squat")
        assert proj is None


# ---------------------------------------------------------------------------
# Training Timeline Retraction
# ---------------------------------------------------------------------------


class TestTrainingTimelineRetraction:
    async def test_retracted_set_excluded(self, db, test_user_id):
        """Retracted set should not appear in training timeline."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "squat", "weight_kg": 100, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "squat", "weight_kg": 110, "reps": 3,
        }, "TIMESTAMP '2026-02-02 10:00:00+01'")

        # Retract first day's set
        await retract_event(db, test_user_id, ev1, "set.logged",
                            "TIMESTAMP '2026-02-03 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_training_timeline(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "training_timeline")
        assert proj is not None
        data = proj["data"]
        assert data["total_training_days"] == 1

    async def test_all_sets_retracted_deletes_projection(self, db, test_user_id):
        """When all training data is retracted, timeline projection should be deleted."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 80, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        # Build projection
        await db.execute("SET ROLE app_worker")
        await update_training_timeline(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        assert await get_projection(db, test_user_id, "training_timeline") is not None
        await db.execute("RESET ROLE")

        # Retract
        await retract_event(db, test_user_id, ev1, "set.logged",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_training_timeline(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "training_timeline") is None


# ---------------------------------------------------------------------------
# Recovery Retraction
# ---------------------------------------------------------------------------


class TestRecoveryRetraction:
    async def test_retracted_sleep_excluded(self, db, test_user_id):
        """Retracted sleep event should be excluded from recovery."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 5.0,
        }, "TIMESTAMP '2026-02-01 07:00:00+01'")
        await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 8.0,
        }, "TIMESTAMP '2026-02-02 07:00:00+01'")

        # Retract the bad night
        await retract_event(db, test_user_id, ev1, "sleep.logged",
                            "TIMESTAMP '2026-02-03 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_recovery(db, {
            "user_id": test_user_id, "event_type": "sleep.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "recovery")
        data = proj["data"]
        assert data["sleep"]["overall"]["avg_duration_hours"] == 8.0
        assert data["sleep"]["overall"]["total_entries"] == 1

    async def test_all_recovery_retracted_deletes_projection(self, db, test_user_id):
        """When all recovery events retracted, projection should be deleted."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 7.5,
        }, "TIMESTAMP '2026-02-01 07:00:00+01'")

        # Build projection
        await db.execute("SET ROLE app_worker")
        await update_recovery(db, {
            "user_id": test_user_id, "event_type": "sleep.logged",
        })
        assert await get_projection(db, test_user_id, "recovery") is not None
        await db.execute("RESET ROLE")

        # Retract
        await retract_event(db, test_user_id, ev1, "sleep.logged",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_recovery(db, {
            "user_id": test_user_id, "event_type": "sleep.logged",
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "recovery") is None

    async def test_retracted_sleep_target_excluded(self, db, test_user_id):
        """Retracted sleep target should be excluded from recovery."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 7.0,
        }, "TIMESTAMP '2026-02-01 07:00:00+01'")
        target_ev = await insert_event(db, test_user_id, "sleep_target.set", {
            "duration_hours": 8, "bedtime": "22:30",
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")
        await retract_event(db, test_user_id, target_ev, "sleep_target.set",
                            "TIMESTAMP '2026-02-03 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_recovery(db, {
            "user_id": test_user_id, "event_type": "sleep.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "recovery")
        data = proj["data"]
        assert "targets" not in data


# ---------------------------------------------------------------------------
# Nutrition Retraction
# ---------------------------------------------------------------------------


class TestNutritionRetraction:
    async def test_retracted_meal_excluded(self, db, test_user_id):
        """Retracted meal should be excluded from nutrition."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "meal.logged", {
            "calories": 9999, "protein_g": 999,
        }, "TIMESTAMP '2026-02-01 12:00:00+01'")
        await insert_event(db, test_user_id, "meal.logged", {
            "calories": 600, "protein_g": 40,
        }, "TIMESTAMP '2026-02-01 18:00:00+01'")

        # Retract the bad entry
        await retract_event(db, test_user_id, ev1, "meal.logged",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_nutrition(db, {
            "user_id": test_user_id, "event_type": "meal.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "nutrition")
        data = proj["data"]
        assert data["total_meals"] == 1
        assert data["daily_totals"][0]["calories"] == 600

    async def test_retracted_nutrition_target_excluded(self, db, test_user_id):
        """Retracted nutrition target should be excluded from nutrition."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "meal.logged", {
            "calories": 500,
        }, "TIMESTAMP '2026-02-01 12:00:00+01'")
        target_ev = await insert_event(db, test_user_id, "nutrition_target.set", {
            "calories": 2000, "protein_g": 150,
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")
        await retract_event(db, test_user_id, target_ev, "nutrition_target.set",
                            "TIMESTAMP '2026-02-03 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_nutrition(db, {
            "user_id": test_user_id, "event_type": "meal.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "nutrition")
        data = proj["data"]
        assert "target" not in data

    async def test_all_meals_retracted_deletes_projection(self, db, test_user_id):
        """When all meals retracted, nutrition projection should be deleted."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "meal.logged", {
            "calories": 500,
        }, "TIMESTAMP '2026-02-01 12:00:00+01'")

        # Build projection
        await db.execute("SET ROLE app_worker")
        await update_nutrition(db, {
            "user_id": test_user_id, "event_type": "meal.logged",
        })
        assert await get_projection(db, test_user_id, "nutrition") is not None
        await db.execute("RESET ROLE")

        # Retract
        await retract_event(db, test_user_id, ev1, "meal.logged",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_nutrition(db, {
            "user_id": test_user_id, "event_type": "meal.logged",
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "nutrition") is None


# ---------------------------------------------------------------------------
# Training Plan Retraction
# ---------------------------------------------------------------------------


class TestTrainingPlanRetraction:
    async def test_retracted_plan_event_excluded(self, db, test_user_id):
        """Retracted plan.created should result in no active plan."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "training_plan.created", {
            "plan_id": "plan-001", "name": "Bad Plan", "sessions": [],
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        # Build projection
        await db.execute("SET ROLE app_worker")
        await update_training_plan(db, {
            "user_id": test_user_id, "event_type": "training_plan.created",
        })
        assert await get_projection(db, test_user_id, "training_plan") is not None
        await db.execute("RESET ROLE")

        # Retract
        await retract_event(db, test_user_id, ev1, "training_plan.created",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_training_plan(db, {
            "user_id": test_user_id, "event_type": "training_plan.created",
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "training_plan") is None


# ---------------------------------------------------------------------------
# User Profile Retraction
# ---------------------------------------------------------------------------


class TestUserProfileRetraction:
    async def test_retracted_profile_event_excluded(self, db, test_user_id):
        """Retracted profile.updated should not appear in user profile."""
        await create_test_user(db, test_user_id)
        ev_wrong = await insert_event(db, test_user_id, "profile.updated", {
            "training_modality": "wrong_value",
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "profile.updated", {
            "training_modality": "strength",
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")

        # Retract the first event
        await retract_event(db, test_user_id, ev_wrong, "profile.updated",
                            "TIMESTAMP '2026-02-03 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_user_profile(db, {
            "user_id": test_user_id, "event_type": "profile.updated",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "user_profile", "me")
        assert proj is not None
        data = proj["data"]
        assert data["user"]["profile"]["training_modality"] == "strength"

    async def test_all_events_retracted_deletes_profile(self, db, test_user_id):
        """When all user events retracted, user_profile should be deleted."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "profile.updated", {
            "name": "Test",
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        # Build projection
        await db.execute("SET ROLE app_worker")
        await update_user_profile(db, {
            "user_id": test_user_id, "event_type": "profile.updated",
        })
        assert await get_projection(db, test_user_id, "user_profile", "me") is not None
        await db.execute("RESET ROLE")

        # Retract
        await retract_event(db, test_user_id, ev1, "profile.updated",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_user_profile(db, {
            "user_id": test_user_id, "event_type": "profile.updated",
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "user_profile", "me") is None


# ---------------------------------------------------------------------------
# Router Retraction Integration
# ---------------------------------------------------------------------------


class TestRouterRetraction:
    async def test_retraction_routes_to_correct_handlers(self, db, test_user_id):
        """event.retracted should trigger re-computation via the router."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 999,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 82.0,
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")

        # Build initial projections
        await db.execute("SET ROLE app_worker")
        await handle_projection_update(db, {
            "event_type": "bodyweight.logged",
            "user_id": test_user_id,
            "event_id": ev1,
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj["data"]["total_weigh_ins"] == 2

        # Retract the bad event
        retraction_id = await retract_event(
            db, test_user_id, ev1, "bodyweight.logged",
            "TIMESTAMP '2026-02-03 08:00:00+01'",
        )

        # Route the retraction through the router
        await db.execute("SET ROLE app_worker")
        await handle_projection_update(db, {
            "event_type": "event.retracted",
            "user_id": test_user_id,
            "event_id": retraction_id,
        })
        await db.execute("RESET ROLE")

        # Projection should now reflect only the valid event
        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj["data"]["total_weigh_ins"] == 1
        assert proj["data"]["current_weight_kg"] == 82.0

    async def test_retraction_without_type_uses_db_lookup(self, db, test_user_id):
        """event.retracted without retracted_event_type should fall back to DB lookup."""
        await create_test_user(db, test_user_id)
        ev1 = await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 999,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 82.0,
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")

        # Build projection
        await db.execute("SET ROLE app_worker")
        await handle_projection_update(db, {
            "event_type": "bodyweight.logged",
            "user_id": test_user_id,
            "event_id": ev1,
        })
        await db.execute("RESET ROLE")

        # Retract WITHOUT retracted_event_type (force DB lookup)
        retraction_id = await insert_event(
            db, test_user_id, "event.retracted",
            {"retracted_event_id": ev1},  # no retracted_event_type
            "TIMESTAMP '2026-02-03 08:00:00+01'",
        )

        await db.execute("SET ROLE app_worker")
        await handle_projection_update(db, {
            "event_type": "event.retracted",
            "user_id": test_user_id,
            "event_id": retraction_id,
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        assert proj["data"]["total_weigh_ins"] == 1
