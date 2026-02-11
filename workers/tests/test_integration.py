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
from kura_workers.handlers.quality_health import update_quality_health
from kura_workers.handlers.readiness_inference import update_readiness_inference
from kura_workers.handlers.session_feedback import update_session_feedback
from kura_workers.handlers.recovery import update_recovery
from kura_workers.handlers.router import handle_projection_retry, handle_projection_update
from kura_workers.handlers.semantic_memory import update_semantic_memory
from kura_workers.handlers.strength_inference import update_strength_inference
from kura_workers.handlers.training_plan import update_training_plan
from kura_workers.handlers.training_timeline import update_training_timeline
from kura_workers.handlers.user_profile import update_user_profile
from kura_workers.eval_harness import run_eval_harness
from kura_workers.scheduler import ensure_nightly_inference_scheduler
from kura_workers.handlers.inference_nightly import handle_inference_nightly_refit
from kura_workers.semantic_bootstrap import ensure_semantic_catalog

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


async def count_events(conn, user_id, event_type, data_key=None, data_value=None):
    query = """
        SELECT COUNT(*) AS count
        FROM events
        WHERE user_id = %s
          AND event_type = %s
    """
    params = [user_id, event_type]
    if data_key is not None:
        query += " AND data->>%s = %s"
        params.extend([data_key, data_value])
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, tuple(params))
        row = await cur.fetchone()
    return int(row["count"])


async def get_latest_inference_run(conn, user_id, projection_type, key):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT *
            FROM inference_runs
            WHERE user_id = %s
              AND projection_type = %s
              AND key = %s
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (user_id, projection_type, key),
        )
        return await cur.fetchone()


async def get_latest_eval_run(conn, user_id):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT *
            FROM inference_eval_runs
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        return await cur.fetchone()


async def get_eval_artifacts(conn, run_id):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT *
            FROM inference_eval_artifacts
            WHERE run_id = %s
            ORDER BY id ASC
            """,
            (run_id,),
        )
        return await cur.fetchall()


async def prepare_nightly_scheduler_for_test(conn) -> None:
    """Normalize global scheduler state to avoid cross-test interference."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE background_jobs
            SET status = 'completed',
                completed_at = NOW()
            WHERE job_type = 'inference.nightly_refit'
              AND status IN ('pending', 'processing')
            """
        )
        await cur.execute(
            """
            INSERT INTO inference_scheduler_state (
                scheduler_key, interval_hours, next_run_at, in_flight_job_id,
                in_flight_started_at, last_run_status, updated_at
            )
            VALUES (
                'nightly_inference_refit', 24, NOW() - make_interval(hours => 1),
                NULL, NULL, 'idle', NOW()
            )
            ON CONFLICT (scheduler_key) DO UPDATE SET
                interval_hours = EXCLUDED.interval_hours,
                next_run_at = EXCLUDED.next_run_at,
                in_flight_job_id = NULL,
                in_flight_started_at = NULL,
                last_run_status = 'idle',
                updated_at = NOW()
            """
        )


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
# Semantic + Inference Foundation
# ---------------------------------------------------------------------------


class TestSemanticMemoryIntegration:
    async def test_semantic_memory_projection_created(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise": "Kniebeuge", "weight_kg": 100, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "meal.logged", {
            "food": "Haferflocken", "calories": 350, "protein_g": 12,
        }, "TIMESTAMP '2026-02-01 12:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await ensure_semantic_catalog(db)
        await update_semantic_memory(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "semantic_memory")
        assert proj is not None
        data = proj["data"]
        assert data["indexed_terms"]["exercise"] >= 1
        assert data["indexed_terms"]["food"] >= 1
        assert "provider" in data
        assert "exercise_candidates" in data
        assert "food_candidates" in data

    async def test_semantic_memory_deletes_when_all_inputs_retracted(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        event_id = await insert_event(db, test_user_id, "set.logged", {
            "exercise": "Kniebeuge", "weight_kg": 100, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await ensure_semantic_catalog(db)
        await update_semantic_memory(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "semantic_memory") is not None

        await retract_event(db, test_user_id, event_id, "set.logged",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_semantic_memory(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "semantic_memory") is None


class TestInferenceIntegration:
    async def test_strength_and_readiness_projections(self, db, test_user_id):
        await create_test_user(db, test_user_id)

        last_set_event_id = None
        for day in range(1, 6):
            await insert_event(db, test_user_id, "sleep.logged", {
                "duration_hours": 7.0 + (day * 0.1),
            }, f"TIMESTAMP '2026-02-{day:02d} 07:00:00+01'")
            await insert_event(db, test_user_id, "energy.logged", {
                "level": 6 + (day % 3),
            }, f"TIMESTAMP '2026-02-{day:02d} 08:00:00+01'")
            await insert_event(db, test_user_id, "soreness.logged", {
                "severity": 2 + (day % 2),
            }, f"TIMESTAMP '2026-02-{day:02d} 09:00:00+01'")
            last_set_event_id = await insert_event(db, test_user_id, "set.logged", {
                "exercise_id": "bench_press",
                "exercise": "Bench Press",
                "weight_kg": 80 + day,
                "reps": 5,
            }, f"TIMESTAMP '2026-02-{day:02d} 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_strength_inference(db, {
            "user_id": test_user_id,
            "event_type": "set.logged",
            "event_id": last_set_event_id,
        })
        await update_readiness_inference(db, {
            "user_id": test_user_id,
            "event_type": "sleep.logged",
        })
        await db.execute("RESET ROLE")

        strength = await get_projection(db, test_user_id, "strength_inference", "bench_press")
        assert strength is not None
        strength_data = strength["data"]
        assert strength_data["exercise_id"] == "bench_press"
        assert strength_data["data_quality"]["sessions_used"] >= 3
        assert "dynamics" in strength_data
        assert "estimated_1rm" in strength_data["dynamics"]
        assert "phase" in strength_data
        assert "weekly_cycle" in strength_data["phase"]

        readiness = await get_projection(db, test_user_id, "readiness_inference")
        assert readiness is not None
        readiness_data = readiness["data"]
        assert readiness_data["data_quality"]["days_with_observations"] >= 5
        assert "readiness_today" in readiness_data
        assert "dynamics" in readiness_data
        assert "readiness" in readiness_data["dynamics"]
        assert "phase" in readiness_data
        assert "weekly_cycle" in readiness_data["phase"]

        strength_run = await get_latest_inference_run(
            db, test_user_id, "strength_inference", "bench_press"
        )
        assert strength_run is not None
        assert strength_run["status"] == "success"
        assert strength_run["engine"] in {"closed_form", "pymc"}

        readiness_run = await get_latest_inference_run(
            db, test_user_id, "readiness_inference", "overview"
        )
        assert readiness_run is not None
        assert readiness_run["status"] == "success"
        assert readiness_run["engine"] == "normal_normal"

    async def test_inference_runs_mark_insufficient_data_as_skipped(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        last_set_event_id = None

        for day in range(1, 3):
            await insert_event(db, test_user_id, "sleep.logged", {
                "duration_hours": 6.8 + (day * 0.1),
            }, f"TIMESTAMP '2026-02-{day:02d} 07:00:00+01'")
            await insert_event(db, test_user_id, "energy.logged", {
                "level": 6 + day,
            }, f"TIMESTAMP '2026-02-{day:02d} 08:00:00+01'")
            await insert_event(db, test_user_id, "soreness.logged", {
                "severity": 2,
            }, f"TIMESTAMP '2026-02-{day:02d} 09:00:00+01'")
            last_set_event_id = await insert_event(db, test_user_id, "set.logged", {
                "exercise_id": "bench_press",
                "exercise": "Bench Press",
                "weight_kg": 85 + day,
                "reps": 5,
            }, f"TIMESTAMP '2026-02-{day:02d} 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_strength_inference(db, {
            "user_id": test_user_id,
            "event_type": "set.logged",
            "event_id": last_set_event_id,
        })
        await update_readiness_inference(db, {
            "user_id": test_user_id,
            "event_type": "sleep.logged",
        })
        await db.execute("RESET ROLE")

        strength_run = await get_latest_inference_run(
            db, test_user_id, "strength_inference", "bench_press"
        )
        assert strength_run is not None
        assert strength_run["status"] == "skipped"
        assert strength_run["diagnostics"]["skip_reason"] == "insufficient_data"
        assert strength_run["diagnostics"]["error_taxonomy"] == "insufficient_data"

        readiness_run = await get_latest_inference_run(
            db, test_user_id, "readiness_inference", "overview"
        )
        assert readiness_run is not None
        assert readiness_run["status"] == "skipped"
        assert readiness_run["diagnostics"]["skip_reason"] == "insufficient_data"
        assert readiness_run["diagnostics"]["error_taxonomy"] == "insufficient_data"

    async def test_eval_harness_replays_event_store_and_persists_artifacts(self, db, test_user_id):
        await create_test_user(db, test_user_id)

        for day in range(1, 9):
            await insert_event(db, test_user_id, "set.logged", {
                "exercise_id": "bench_press",
                "exercise": "Bench Press",
                "weight_kg": 90 + day,
                "reps": 5,
            }, f"TIMESTAMP '2026-01-{day:02d} 10:00:00+01'")
            await insert_event(db, test_user_id, "sleep.logged", {
                "duration_hours": 7.0 + (day * 0.05),
            }, f"TIMESTAMP '2026-02-{day:02d} 07:00:00+01'")
            await insert_event(db, test_user_id, "energy.logged", {
                "level": 6 + (day % 3),
            }, f"TIMESTAMP '2026-02-{day:02d} 08:00:00+01'")
            await insert_event(db, test_user_id, "soreness.logged", {
                "severity": 2 + (day % 2),
            }, f"TIMESTAMP '2026-02-{day:02d} 09:00:00+01'")

        await db.execute("SET ROLE app_worker")
        result = await run_eval_harness(
            db,
            user_id=test_user_id,
            source="event_store",
            strength_engine="closed_form",
            persist=True,
        )
        await db.execute("RESET ROLE")

        assert result["source"] == "event_store"
        assert result["projection_rows"] >= 1
        assert "run_id" in result

        run_row = await get_latest_eval_run(db, test_user_id)
        assert run_row is not None
        assert str(run_row["id"]) == result["run_id"]
        assert run_row["source"] == "event_store"
        assert run_row["status"] in {"completed", "failed"}

        artifacts = await get_eval_artifacts(db, result["run_id"])
        assert len(artifacts) >= 1
        assert all(a["source"] == "event_store" for a in artifacts)

    async def test_eval_harness_combined_source_reports_by_source(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        last_set_event_id = None
        for day in range(1, 7):
            await insert_event(db, test_user_id, "sleep.logged", {
                "duration_hours": 7.1 + (day * 0.05),
            }, f"TIMESTAMP '2026-02-{day:02d} 07:00:00+01'")
            await insert_event(db, test_user_id, "energy.logged", {
                "level": 6 + (day % 3),
            }, f"TIMESTAMP '2026-02-{day:02d} 08:00:00+01'")
            await insert_event(db, test_user_id, "soreness.logged", {
                "severity": 2 + (day % 2),
            }, f"TIMESTAMP '2026-02-{day:02d} 09:00:00+01'")
            last_set_event_id = await insert_event(db, test_user_id, "set.logged", {
                "exercise_id": "bench_press",
                "exercise": "Bench Press",
                "weight_kg": 85 + day,
                "reps": 5,
            }, f"TIMESTAMP '2026-02-{day:02d} 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_strength_inference(db, {
            "user_id": test_user_id,
            "event_type": "set.logged",
            "event_id": last_set_event_id,
        })
        await update_readiness_inference(db, {
            "user_id": test_user_id,
            "event_type": "sleep.logged",
        })
        result = await run_eval_harness(
            db,
            user_id=test_user_id,
            source="both",
            strength_engine="closed_form",
            persist=False,
        )
        await db.execute("RESET ROLE")

        assert result["source"] == "both"
        assert "summary_by_source" in result
        assert "projection_history" in result["summary_by_source"]
        assert "event_store" in result["summary_by_source"]

    async def test_strength_projection_deleted_when_all_sets_retracted(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        event_ids = []
        for day in range(1, 4):
            event_id = await insert_event(db, test_user_id, "set.logged", {
                "exercise_id": "bench_press",
                "exercise": "Bench Press",
                "weight_kg": 80 + day,
                "reps": 5,
            }, f"TIMESTAMP '2026-02-{day:02d} 10:00:00+01'")
            event_ids.append(event_id)

        await db.execute("SET ROLE app_worker")
        await update_strength_inference(db, {
            "user_id": test_user_id,
            "event_type": "set.logged",
            "event_id": event_ids[-1],
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "strength_inference", "bench_press") is not None

        for event_id in event_ids:
            await retract_event(db, test_user_id, event_id, "set.logged",
                                "TIMESTAMP '2026-02-10 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_strength_inference(db, {
            "user_id": test_user_id,
            "event_type": "set.logged",
            "event_id": event_ids[-1],
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "strength_inference", "bench_press") is None

    async def test_readiness_projection_deleted_when_all_signals_retracted(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        inserted_events = []

        for day in range(1, 6):
            sleep_event_id = await insert_event(db, test_user_id, "sleep.logged", {
                "duration_hours": 7.0 + (day * 0.1),
            }, f"TIMESTAMP '2026-02-{day:02d} 07:00:00+01'")
            inserted_events.append((sleep_event_id, "sleep.logged"))

            energy_event_id = await insert_event(db, test_user_id, "energy.logged", {
                "level": 6 + (day % 3),
            }, f"TIMESTAMP '2026-02-{day:02d} 08:00:00+01'")
            inserted_events.append((energy_event_id, "energy.logged"))

            soreness_event_id = await insert_event(db, test_user_id, "soreness.logged", {
                "severity": 2 + (day % 2),
            }, f"TIMESTAMP '2026-02-{day:02d} 09:00:00+01'")
            inserted_events.append((soreness_event_id, "soreness.logged"))

        await db.execute("SET ROLE app_worker")
        await update_readiness_inference(db, {
            "user_id": test_user_id,
            "event_type": "sleep.logged",
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "readiness_inference") is not None

        for event_id, event_type in inserted_events:
            await retract_event(
                db,
                test_user_id,
                event_id,
                event_type,
                "TIMESTAMP '2026-02-10 08:00:00+01'",
            )

        await db.execute("SET ROLE app_worker")
        await update_readiness_inference(db, {
            "user_id": test_user_id,
            "event_type": "sleep.logged",
        })
        await db.execute("RESET ROLE")

        assert await get_projection(db, test_user_id, "readiness_inference") is None


class TestInferenceNightlyRefitIntegration:
    async def test_nightly_refit_enqueues_projection_updates(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 90, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 7.5,
        }, "TIMESTAMP '2026-02-01 07:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await prepare_nightly_scheduler_for_test(db)
        await db.execute("RESET ROLE")

        async with db.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM background_jobs
                WHERE user_id = %s
                  AND job_type = 'projection.update'
                """,
                (test_user_id,),
            )
            projection_count_before = int((await cur.fetchone())["count"])

            await cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM background_jobs
                WHERE job_type = 'inference.nightly_refit'
                  AND payload->>'interval_hours' = '12'
                """,
            )
            nightly_count_before = int((await cur.fetchone())["count"])

        await db.execute("SET ROLE app_worker")
        await handle_inference_nightly_refit(db, {"interval_hours": 12})
        await db.execute("RESET ROLE")

        async with db.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT payload
                FROM background_jobs
                WHERE user_id = %s
                  AND job_type = 'projection.update'
                """,
                (test_user_id,),
            )
            projection_rows = await cur.fetchall()

            await cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM background_jobs
                WHERE job_type = 'inference.nightly_refit'
                  AND payload->>'interval_hours' = '12'
                """,
            )
            nightly_count_after = int((await cur.fetchone())["count"])

        assert len(projection_rows) - projection_count_before == 2
        event_types = {row["payload"]["event_type"] for row in projection_rows}
        assert "set.logged" in event_types
        assert "sleep.logged" in event_types

        assert nightly_count_after - nightly_count_before == 0

    async def test_nightly_refit_deduplicates_projection_update_jobs(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 90, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 7.5,
        }, "TIMESTAMP '2026-02-01 07:00:00+01'")

        payload = {"interval_hours": 12, "scheduler_key": "nightly_inference_refit"}

        await db.execute("SET ROLE app_worker")
        await prepare_nightly_scheduler_for_test(db)
        await handle_inference_nightly_refit(db, payload)
        await handle_inference_nightly_refit(db, payload)
        await db.execute("RESET ROLE")

        async with db.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT payload->>'event_type' AS event_type
                FROM background_jobs
                WHERE user_id = %s
                  AND job_type = 'projection.update'
                  AND payload->>'source' = 'inference.nightly_refit'
                  AND status IN ('pending', 'processing')
                """,
                (test_user_id,),
            )
            rows = await cur.fetchall()

        assert len(rows) == 2
        event_types = {row["event_type"] for row in rows}
        assert event_types == {"set.logged", "sleep.logged"}

    async def test_durable_scheduler_recovers_after_failed_in_flight_job(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 95, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await prepare_nightly_scheduler_for_test(db)
        await ensure_nightly_inference_scheduler(db)

        async with db.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT in_flight_job_id, next_run_at
                FROM inference_scheduler_state
                WHERE scheduler_key = 'nightly_inference_refit'
                """,
            )
            first_state = await cur.fetchone()
        assert first_state is not None
        first_job_id = int(first_state["in_flight_job_id"])

        async with db.cursor() as cur:
            await cur.execute(
                """
                UPDATE background_jobs
                SET status = 'dead',
                    error_message = 'simulated nightly failure',
                    completed_at = NOW()
                WHERE id = %s
                """,
                (first_job_id,),
            )

        await ensure_nightly_inference_scheduler(db)
        await db.execute("RESET ROLE")

        async with db.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT in_flight_job_id, last_run_status, last_error
                FROM inference_scheduler_state
                WHERE scheduler_key = 'nightly_inference_refit'
                """,
            )
            recovered_state = await cur.fetchone()
        assert recovered_state is not None
        assert int(recovered_state["in_flight_job_id"]) != first_job_id
        assert recovered_state["last_run_status"] == "running"

    async def test_durable_scheduler_records_missed_run_catch_up(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 100, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await prepare_nightly_scheduler_for_test(db)
        await ensure_nightly_inference_scheduler(db)

        async with db.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT in_flight_job_id
                FROM inference_scheduler_state
                WHERE scheduler_key = 'nightly_inference_refit'
                """,
            )
            initial_state = await cur.fetchone()
        assert initial_state is not None
        initial_job_id = int(initial_state["in_flight_job_id"])

        async with db.cursor() as cur:
            await cur.execute(
                """
                UPDATE background_jobs
                SET status = 'completed', completed_at = NOW()
                WHERE id = %s
                """,
                (initial_job_id,),
            )

            await cur.execute(
                """
                UPDATE inference_scheduler_state
                SET next_run_at = NOW() - make_interval(hours => interval_hours * 3)
                WHERE scheduler_key = 'nightly_inference_refit'
                """
            )

        await ensure_nightly_inference_scheduler(db)
        await db.execute("RESET ROLE")

        async with db.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT in_flight_job_id, last_missed_runs
                FROM inference_scheduler_state
                WHERE scheduler_key = 'nightly_inference_refit'
                """,
            )
            catch_up_state = await cur.fetchone()
        assert catch_up_state is not None
        catch_up_job_id = int(catch_up_state["in_flight_job_id"])
        assert catch_up_job_id != initial_job_id
        assert int(catch_up_state["last_missed_runs"]) >= 1

        async with db.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT payload
                FROM background_jobs
                WHERE id = %s
                """,
                (catch_up_job_id,),
            )
            catch_up_job = await cur.fetchone()
        assert catch_up_job is not None
        assert int(catch_up_job["payload"]["missed_runs"]) >= 1


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

        # user + agenda structure (system layer is in system_config table)
        assert "system" not in data
        assert "user" in data
        assert "agenda" in data

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
# Quality Health (Decision 13 Phase 0)
# ---------------------------------------------------------------------------


class TestQualityHealthIntegration:
    async def test_detects_policy_gated_invariant_issues(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise": "Mystery Cable Move", "weight_kg": 25, "reps": 10,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "goal.set", {
            "description": "Ich will dunken koennen",
        }, "TIMESTAMP '2026-02-01 11:00:00+01'")
        await insert_event(db, test_user_id, "preference.set", {
            "key": "unit_system", "value": "metric",
        }, "TIMESTAMP '2026-02-01 12:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_quality_health(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "quality_health")
        assert proj is not None
        data = proj["data"]
        assert data["invariant_mode"] == "policy_gated_auto_apply"
        assert data["repair_apply_enabled"] is False
        assert data["integrity_slo_status"] == "degraded"
        assert data["autonomy_policy"]["throttle_active"] is True
        assert data["autonomy_policy"]["max_scope_level"] == "strict"
        assert data["issues_open"] >= 1
        assert data["score"] < 1.0
        issue_types = {issue["type"] for issue in data["issues"]}
        assert "timezone_missing" in issue_types
        assert data["repair_proposals_total"] >= 1
        assert data["simulate_bridge"]["target_endpoint"] == "/v1/events/simulate"
        assert data["simulate_bridge"]["decision_phase"] == "phase_2_autonomous_tier_a"
        assert data["repair_apply_results_by_decision"]["applied"] == 0
        proposal_states = {proposal["state"] for proposal in data["repair_proposals"]}
        assert "simulated_risky" in proposal_states

    async def test_tier_a_auto_apply_closes_issue_and_emits_events(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise": "bench press", "weight_kg": 90, "reps": 6,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "preference.set", {
            "key": "timezone", "value": "Europe/Berlin",
        }, "TIMESTAMP '2026-02-01 11:00:00+01'")
        await insert_event(db, test_user_id, "profile.updated", {
            "age_deferred": True, "bodyweight_deferred": True,
        }, "TIMESTAMP '2026-02-01 12:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_quality_health(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "quality_health")
        assert proj is not None
        data = proj["data"]
        assert data["issues_open"] == 0
        assert data["repair_apply_results_by_decision"]["applied"] >= 1
        assert data["last_repair_at"] is not None

        alias_events = await count_events(
            db,
            test_user_id,
            "exercise.alias_created",
            data_key="alias",
            data_value="bench press",
        )
        assert alias_events == 1
        assert await count_events(db, test_user_id, "quality.fix.applied") >= 1
        assert await count_events(db, test_user_id, "quality.issue.closed") >= 1

    async def test_auto_apply_recurrence_guard_is_idempotent(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise": "bench press", "weight_kg": 85, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "preference.set", {
            "key": "timezone", "value": "Europe/Berlin",
        }, "TIMESTAMP '2026-02-01 11:00:00+01'")
        await insert_event(db, test_user_id, "profile.updated", {
            "age_deferred": True, "bodyweight_deferred": True,
        }, "TIMESTAMP '2026-02-01 12:00:00+01'")

        await db.execute("SET ROLE app_worker")
        payload = {"user_id": test_user_id, "event_type": "set.logged"}
        await update_quality_health(db, payload)
        await update_quality_health(db, payload)
        await db.execute("RESET ROLE")

        alias_events = await count_events(
            db,
            test_user_id,
            "exercise.alias_created",
            data_key="alias",
            data_value="bench press",
        )
        assert alias_events == 1
        assert await count_events(db, test_user_id, "quality.fix.applied") == 1
        assert await count_events(db, test_user_id, "quality.issue.closed") == 1

    async def test_risky_repairs_are_rejected_without_apply(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "barbell_back_squat", "weight_kg": 100, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "profile.updated", {
            "age_deferred": True, "bodyweight_deferred": True,
        }, "TIMESTAMP '2026-02-01 11:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_quality_health(db, {
            "user_id": test_user_id, "event_type": "set.logged",
        })
        await db.execute("RESET ROLE")

        assert await count_events(
            db,
            test_user_id,
            "preference.set",
            data_key="key",
            data_value="timezone",
        ) == 0
        assert await count_events(db, test_user_id, "quality.fix.rejected") >= 1

    async def test_save_claim_mismatch_slo_drives_autonomy_throttle(self, db, test_user_id):
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "barbell_back_squat", "weight_kg": 105, "reps": 5,
        }, "TIMESTAMP '2026-02-11 09:00:00+00'")
        await insert_event(db, test_user_id, "preference.set", {
            "key": "timezone", "value": "Europe/Berlin",
        }, "TIMESTAMP '2026-02-11 09:05:00+00'")
        await insert_event(db, test_user_id, "profile.updated", {
            "age_deferred": True, "bodyweight_deferred": True,
        }, "TIMESTAMP '2026-02-11 09:10:00+00'")
        await insert_event(db, test_user_id, "quality.save_claim.checked", {
            "mismatch_detected": True, "allow_saved_claim": False,
        }, "TIMESTAMP '2026-02-11 09:15:00+00'")
        await insert_event(db, test_user_id, "quality.save_claim.checked", {
            "mismatch_detected": False, "allow_saved_claim": True,
        }, "TIMESTAMP '2026-02-11 09:20:00+00'")

        await db.execute("SET ROLE app_worker")
        await update_quality_health(db, {
            "user_id": test_user_id, "event_type": "quality.save_claim.checked",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "quality_health")
        assert proj is not None
        data = proj["data"]
        mismatch = data["integrity_slos"]["metrics"]["save_claim_mismatch_rate_pct"]
        assert mismatch["sample_count"] == 2
        assert mismatch["mismatch_count"] == 1
        assert mismatch["value"] == 50.0
        assert data["integrity_slo_status"] == "degraded"
        assert data["autonomy_policy"]["throttle_active"] is True


# ---------------------------------------------------------------------------
# Session Feedback (Decision 13 — PDC.8)
# ---------------------------------------------------------------------------


class TestSessionFeedbackIntegration:
    async def test_session_completed_creates_feedback_projection(self, db, test_user_id):
        """session.completed events should produce a session_feedback projection."""
        await create_test_user(db, test_user_id)

        # Log training sets first (for load aggregation)
        await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 80, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 85, "reps": 3,
        }, "TIMESTAMP '2026-02-01 10:10:00+01'")

        # Now log session feedback
        await insert_event(db, test_user_id, "session.completed", {
            "enjoyment": 4, "perceived_quality": 3,
            "perceived_exertion": 7, "notes": "Felt strong today",
        }, "TIMESTAMP '2026-02-01 11:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_session_feedback(db, {
            "user_id": test_user_id, "event_type": "session.completed",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "session_feedback")
        assert proj is not None
        data = proj["data"]
        assert data["counts"]["sessions_with_feedback"] == 1
        recent = data["recent_sessions"]
        assert len(recent) == 1
        assert recent[0]["enjoyment"] == 4.0
        assert recent[0]["perceived_quality"] == 3.0
        assert recent[0]["perceived_exertion"] == 7.0
        assert recent[0]["context"] == "Felt strong today"
        # Session load joined from set.logged by date
        assert recent[0]["session_load"]["total_sets"] == 2
        assert recent[0]["session_load"]["total_volume_kg"] > 0

    async def test_session_completed_with_session_id_joins_load(self, db, test_user_id):
        """session_id-based join between feedback and sets."""
        await create_test_user(db, test_user_id)
        session_id = "sess-abc-001"

        # Two sets with explicit session_id
        for i in range(2):
            event_id = str(uuid.uuid4())
            idem_key = str(uuid.uuid4())
            async with db.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO events (id, user_id, event_type, data, metadata, timestamp)
                    VALUES (%s, %s, 'set.logged', %s, %s, TIMESTAMP '2026-02-05 10:00:00+01' + make_interval(mins => %s))
                    """,
                    (
                        event_id, test_user_id,
                        psycopg.types.json.Json({"exercise_id": "squat", "weight_kg": 100 + i * 10, "reps": 5}),
                        psycopg.types.json.Json({"idempotency_key": idem_key, "session_id": session_id}),
                        i * 5,
                    ),
                )

        # Feedback with matching session_id
        fb_id = str(uuid.uuid4())
        fb_idem = str(uuid.uuid4())
        async with db.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO events (id, user_id, event_type, data, metadata, timestamp)
                VALUES (%s, %s, 'session.completed', %s, %s, TIMESTAMP '2026-02-05 11:00:00+01')
                """,
                (
                    fb_id, test_user_id,
                    psycopg.types.json.Json({"enjoyment": 5}),
                    psycopg.types.json.Json({"idempotency_key": fb_idem, "session_id": session_id}),
                ),
            )

        await db.execute("SET ROLE app_worker")
        await update_session_feedback(db, {
            "user_id": test_user_id, "event_type": "session.completed",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "session_feedback")
        assert proj is not None
        recent = proj["data"]["recent_sessions"]
        assert len(recent) == 1
        assert recent[0]["session_id"] == session_id
        assert recent[0]["session_load"]["total_sets"] == 2

    async def test_legacy_text_infers_enjoyment(self, db, test_user_id):
        """Free-text 'feeling' field without explicit scores should infer enjoyment."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "session.completed", {
            "feeling": "Training war heute richtig schlecht und müde",
        }, "TIMESTAMP '2026-02-03 18:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_session_feedback(db, {
            "user_id": test_user_id, "event_type": "session.completed",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "session_feedback")
        recent = proj["data"]["recent_sessions"]
        assert len(recent) == 1
        # "schlecht" and "müde" are negative hints → enjoyment = 2.0
        assert recent[0]["enjoyment"] == 2.0
        assert recent[0]["context"] == "Training war heute richtig schlecht und müde"


class TestSetCorrectionIntegration:
    async def test_set_corrected_updates_exercise_progression(self, db, test_user_id):
        """set.corrected should modify effective data in exercise_progression."""
        await create_test_user(db, test_user_id)
        set_event_id = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 80, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        # Correction: weight was actually 85 kg
        correction_event_id = await insert_event(db, test_user_id, "set.corrected", {
            "target_event_id": set_event_id,
            "changed_fields": {
                "weight_kg": {
                    "value": 85,
                    "repair_provenance": {
                        "source_type": "inferred",
                        "confidence": 0.92,
                        "confidence_band": "high",
                        "applies_scope": "single_set",
                        "reason": "mention-bound rest extraction",
                    },
                },
            },
            "reason": "weight misread",
        }, "TIMESTAMP '2026-02-01 10:05:00+01'")

        await db.execute("SET ROLE app_worker")
        # event_id must be the correction event's ID (handler reads target_event_id from it)
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "set.corrected",
            "event_id": correction_event_id,
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "exercise_progression", "bench_press")
        assert proj is not None
        data = proj["data"]
        # Volume should reflect corrected weight: 85 * 5 = 425
        assert data["total_volume_kg"] == 425.0
        # 1RM should be based on 85kg x 5 (Epley)
        assert data["estimated_1rm"] > 0

    async def test_set_corrected_affects_session_feedback_load(self, db, test_user_id):
        """set.corrected should update the session load in session_feedback."""
        await create_test_user(db, test_user_id)
        set_event_id = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "squat", "weight_kg": 100, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")
        await insert_event(db, test_user_id, "session.completed", {
            "enjoyment": 4,
        }, "TIMESTAMP '2026-02-01 11:00:00+01'")

        # Correct weight upward
        await insert_event(db, test_user_id, "set.corrected", {
            "target_event_id": set_event_id,
            "changed_fields": {"weight_kg": 120},
            "reason": "typo fix",
        }, "TIMESTAMP '2026-02-01 11:05:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_session_feedback(db, {
            "user_id": test_user_id, "event_type": "set.corrected",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "session_feedback")
        assert proj is not None
        load = proj["data"]["recent_sessions"][0]["session_load"]
        # Corrected: 120 * 5 = 600
        assert load["total_volume_kg"] == 600.0


class TestSessionFeedbackIdempotency:
    async def test_double_handler_call_produces_same_data(self, db, test_user_id):
        """Running session_feedback handler twice produces identical projection data."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "session.completed", {
            "enjoyment": 3, "perceived_exertion": 6,
        }, "TIMESTAMP '2026-02-01 18:00:00+01'")

        await db.execute("SET ROLE app_worker")

        payload = {"user_id": test_user_id, "event_type": "session.completed"}
        await update_session_feedback(db, payload)
        proj1 = await get_projection(db, test_user_id, "session_feedback")

        await update_session_feedback(db, payload)
        proj2 = await get_projection(db, test_user_id, "session_feedback")

        await db.execute("RESET ROLE")

        assert proj1["data"] == proj2["data"]
        assert proj2["version"] == proj1["version"] + 1


class TestSessionFeedbackRetraction:
    async def test_retract_session_completed_removes_projection(self, db, test_user_id):
        """Retracting the only session.completed event should delete the projection."""
        await create_test_user(db, test_user_id)
        event_id = await insert_event(db, test_user_id, "session.completed", {
            "enjoyment": 4, "notes": "Good session",
        }, "TIMESTAMP '2026-02-01 18:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_session_feedback(db, {
            "user_id": test_user_id, "event_type": "session.completed",
        })
        await db.execute("RESET ROLE")

        # Verify projection exists
        proj = await get_projection(db, test_user_id, "session_feedback")
        assert proj is not None

        # Retract the event
        await retract_event(db, test_user_id, event_id, "session.completed",
                            "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_session_feedback(db, {
            "user_id": test_user_id, "event_type": "session.completed",
        })
        await db.execute("RESET ROLE")

        # Projection should be deleted
        proj = await get_projection(db, test_user_id, "session_feedback")
        assert proj is None

    async def test_retract_one_of_many_keeps_remaining(self, db, test_user_id):
        """Retracting one session from several should keep the others."""
        await create_test_user(db, test_user_id)
        event_1 = await insert_event(db, test_user_id, "session.completed", {
            "enjoyment": 3,
        }, "TIMESTAMP '2026-02-01 18:00:00+01'")
        await insert_event(db, test_user_id, "session.completed", {
            "enjoyment": 5,
        }, "TIMESTAMP '2026-02-02 18:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_session_feedback(db, {
            "user_id": test_user_id, "event_type": "session.completed",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "session_feedback")
        assert proj["data"]["counts"]["sessions_with_feedback"] == 2

        # Retract first event
        await retract_event(db, test_user_id, event_1, "session.completed",
                            "TIMESTAMP '2026-02-03 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_session_feedback(db, {
            "user_id": test_user_id, "event_type": "session.completed",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "session_feedback")
        assert proj is not None
        assert proj["data"]["counts"]["sessions_with_feedback"] == 1
        assert proj["data"]["recent_sessions"][0]["enjoyment"] == 5.0


class TestSessionCompletedNotOrphan:
    async def test_session_completed_not_in_orphaned_event_types(self, db, test_user_id):
        """session.completed is handled by session_feedback, so NOT an orphan."""
        await create_test_user(db, test_user_id)
        # Log a session.completed + a known event type
        await insert_event(db, test_user_id, "session.completed", {
            "enjoyment": 4,
        }, "TIMESTAMP '2026-02-01 18:00:00+01'")
        await insert_event(db, test_user_id, "profile.updated", {
            "name": "Test",
        }, "TIMESTAMP '2026-02-01 07:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_user_profile(db, {
            "user_id": test_user_id, "event_type": "session.completed",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "user_profile", "me")
        assert proj is not None
        data = proj["data"]
        data_quality = data["user"]["data_quality"]
        orphans = data_quality.get("orphaned_event_types", [])
        orphan_types = [o["event_type"] for o in orphans]
        assert "session.completed" not in orphan_types


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
        assert "user" in up["data"]
        assert "agenda" in up["data"]


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


# ---------------------------------------------------------------------------
# Anomaly Detection
# ---------------------------------------------------------------------------


class TestAnomalyDetectionIntegration:
    async def test_normal_data_has_empty_anomalies(self, db, test_user_id):
        """Normal data should produce data_quality with empty anomalies."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 82.5,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        data = proj["data"]
        assert "data_quality" in data
        assert data["data_quality"]["anomalies"] == []

    async def test_bodyweight_out_of_range_flagged(self, db, test_user_id):
        """Bodyweight of 500kg should be flagged as anomaly."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 500,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        anomalies = proj["data"]["data_quality"]["anomalies"]
        assert len(anomalies) == 1
        assert anomalies[0]["field"] == "weight_kg"
        assert anomalies[0]["value"] == 500

    async def test_bodyweight_sudden_change_flagged(self, db, test_user_id):
        """Weight change > 5kg in 1 day should be flagged."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 80,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 150,
        }, "TIMESTAMP '2026-02-02 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        anomalies = proj["data"]["data_quality"]["anomalies"]
        # Should have at least the day-over-day change anomaly
        change_anomalies = [a for a in anomalies if "changed" in a["message"]]
        assert len(change_anomalies) >= 1

    async def test_exercise_progression_has_data_quality(self, db, test_user_id):
        """Exercise progression should include data_quality."""
        await create_test_user(db, test_user_id)
        event_id = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "bench_press", "weight_kg": 80, "reps": 5,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "set.logged",
            "event_id": event_id,
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "exercise_progression", "bench_press")
        data = proj["data"]
        assert "data_quality" in data
        assert data["data_quality"]["anomalies"] == []

    async def test_exercise_extreme_weight_flagged(self, db, test_user_id):
        """600kg squat should be flagged."""
        await create_test_user(db, test_user_id)
        event_id = await insert_event(db, test_user_id, "set.logged", {
            "exercise_id": "squat", "weight_kg": 600, "reps": 1,
        }, "TIMESTAMP '2026-02-01 10:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_exercise_progression(db, {
            "user_id": test_user_id, "event_type": "set.logged",
            "event_id": event_id,
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "exercise_progression", "squat")
        anomalies = proj["data"]["data_quality"]["anomalies"]
        assert len(anomalies) >= 1
        assert anomalies[0]["field"] == "weight_kg"

    async def test_nutrition_extreme_calories_flagged(self, db, test_user_id):
        """Meal with 8000 kcal should be flagged."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "meal.logged", {
            "calories": 8000, "protein_g": 50,
        }, "TIMESTAMP '2026-02-01 12:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_nutrition(db, {
            "user_id": test_user_id, "event_type": "meal.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "nutrition")
        anomalies = proj["data"]["data_quality"]["anomalies"]
        assert len(anomalies) >= 1
        assert anomalies[0]["field"] == "calories"

    async def test_recovery_normal_data_no_anomalies(self, db, test_user_id):
        """Normal recovery data should not flag anomalies."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 7.5, "quality": "good",
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
        assert proj["data"]["data_quality"]["anomalies"] == []

    async def test_recovery_extreme_sleep_flagged(self, db, test_user_id):
        """25 hours of sleep should be flagged."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "sleep.logged", {
            "duration_hours": 25,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_recovery(db, {
            "user_id": test_user_id, "event_type": "sleep.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "recovery")
        anomalies = proj["data"]["data_quality"]["anomalies"]
        assert len(anomalies) >= 1
        assert anomalies[0]["field"] == "duration_hours"

    async def test_anomaly_still_processes_event(self, db, test_user_id):
        """Anomalous events should still be included in the projection — anomalies are warnings."""
        await create_test_user(db, test_user_id)
        await insert_event(db, test_user_id, "bodyweight.logged", {
            "weight_kg": 500,
        }, "TIMESTAMP '2026-02-01 08:00:00+01'")

        await db.execute("SET ROLE app_worker")
        await update_body_composition(db, {
            "user_id": test_user_id, "event_type": "bodyweight.logged",
        })
        await db.execute("RESET ROLE")

        proj = await get_projection(db, test_user_id, "body_composition")
        data = proj["data"]
        # Event is processed despite being anomalous
        assert data["current_weight_kg"] == 500
        assert data["total_weigh_ins"] == 1
        # But anomaly is flagged
        assert len(data["data_quality"]["anomalies"]) == 1
