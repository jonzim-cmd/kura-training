"""Tests for novel field generators (Phase 2 pattern detection data)."""

import random

from datagen.engine import SimulationEngine
from datagen.exercises import EXERCISES
from datagen.generators.novel_fields import (
    generate_cardio,
    generate_supplements,
    novel_energy_fields,
    novel_meal_fields,
    novel_set_fields,
    novel_sleep_fields,
)
from datagen.models import AthleteState
from datagen.presets import ADVANCED, BEGINNER, INTERMEDIATE


# ---------------------------------------------------------------------------
# novel_set_fields
# ---------------------------------------------------------------------------


class TestNovelSetFields:
    def test_beginner_only_rest_seconds(self):
        """Beginner should only get rest_seconds, never tempo or bar_speed."""
        rng = random.Random(42)
        ex = EXERCISES["lateral_raise"]  # isolation
        results = [
            novel_set_fields(BEGINNER, ex, "working", rng)
            for _ in range(100)
        ]
        all_keys = {k for r in results for k in r}
        assert "tempo" not in all_keys
        assert "bar_speed" not in all_keys
        # rest_seconds should appear at least sometimes (30% prob)
        rest_count = sum(1 for r in results if "rest_seconds" in r)
        assert rest_count > 0

    def test_beginner_no_fields_on_warmup(self):
        rng = random.Random(42)
        ex = EXERCISES["barbell_back_squat"]
        results = [
            novel_set_fields(BEGINNER, ex, "warmup", rng)
            for _ in range(50)
        ]
        assert all(r == {} for r in results)

    def test_intermediate_tempo_on_isolation(self):
        rng = random.Random(42)
        ex = EXERCISES["lateral_raise"]  # isolation
        results = [
            novel_set_fields(INTERMEDIATE, ex, "working", rng)
            for _ in range(100)
        ]
        tempo_count = sum(1 for r in results if "tempo" in r)
        # 80% probability on isolation
        assert tempo_count > 50

    def test_intermediate_no_bar_speed(self):
        rng = random.Random(42)
        ex = EXERCISES["barbell_back_squat"]
        results = [
            novel_set_fields(INTERMEDIATE, ex, "working", rng)
            for _ in range(100)
        ]
        assert all("bar_speed" not in r for r in results)

    def test_advanced_bar_speed_on_compounds(self):
        rng = random.Random(42)
        ex = EXERCISES["barbell_back_squat"]  # compound
        results = [
            novel_set_fields(ADVANCED, ex, "working", rng)
            for _ in range(100)
        ]
        speed_count = sum(1 for r in results if "bar_speed" in r)
        assert speed_count > 0
        # Verify realistic values
        for r in results:
            if "bar_speed" in r:
                assert 0.3 <= r["bar_speed"] <= 1.2

    def test_advanced_tempo_on_compounds_sometimes(self):
        rng = random.Random(42)
        ex = EXERCISES["barbell_bench_press"]  # compound
        results = [
            novel_set_fields(ADVANCED, ex, "working", rng)
            for _ in range(100)
        ]
        tempo_count = sum(1 for r in results if "tempo" in r)
        # 30% probability on compounds for advanced
        assert 10 < tempo_count < 60

    def test_rest_seconds_realistic_values(self):
        rng = random.Random(42)
        ex = EXERCISES["barbell_back_squat"]
        results = [
            novel_set_fields(ADVANCED, ex, "working", rng)
            for _ in range(100)
        ]
        for r in results:
            if "rest_seconds" in r:
                assert r["rest_seconds"] in (90, 120, 150, 180, 240, 300)

    def test_tempo_format(self):
        rng = random.Random(42)
        ex = EXERCISES["barbell_curl"]  # isolation
        results = [
            novel_set_fields(ADVANCED, ex, "working", rng)
            for _ in range(100)
        ]
        for r in results:
            if "tempo" in r:
                assert len(r["tempo"]) == 4
                assert r["tempo"].isdigit()


# ---------------------------------------------------------------------------
# novel_sleep_fields
# ---------------------------------------------------------------------------


class TestNovelSleepFields:
    def test_beginner_no_wearable(self):
        rng = random.Random(42)
        assert novel_sleep_fields(BEGINNER, rng, 7.5) == {}

    def test_intermediate_has_hrv_and_deep_sleep(self):
        rng = random.Random(42)
        result = novel_sleep_fields(INTERMEDIATE, rng, 7.5)
        assert "hrv_rmssd" in result
        assert "deep_sleep_pct" in result
        assert "awakenings" not in result
        assert 0 < result["hrv_rmssd"] < 150
        assert 0 < result["deep_sleep_pct"] < 100

    def test_advanced_has_all(self):
        rng = random.Random(42)
        result = novel_sleep_fields(ADVANCED, rng, 7.5)
        assert "hrv_rmssd" in result
        assert "deep_sleep_pct" in result
        assert "awakenings" in result
        assert result["awakenings"] >= 0


# ---------------------------------------------------------------------------
# novel_energy_fields
# ---------------------------------------------------------------------------


class TestNovelEnergyFields:
    def test_beginner_no_extra(self):
        rng = random.Random(42)
        results = [novel_energy_fields(BEGINNER, rng) for _ in range(100)]
        assert all(r == {} for r in results)

    def test_intermediate_sometimes_stress(self):
        rng = random.Random(42)
        results = [novel_energy_fields(INTERMEDIATE, rng) for _ in range(100)]
        stress_count = sum(1 for r in results if "stress_level" in r)
        assert stress_count > 0
        # No caffeine for intermediate
        assert all("caffeine_mg" not in r for r in results)

    def test_advanced_stress_and_caffeine(self):
        rng = random.Random(42)
        results = [novel_energy_fields(ADVANCED, rng) for _ in range(100)]
        stress_count = sum(1 for r in results if "stress_level" in r)
        caffeine_count = sum(1 for r in results if "caffeine_mg" in r)
        assert stress_count > 0
        assert caffeine_count > 0
        for r in results:
            if "stress_level" in r:
                assert 1 <= r["stress_level"] <= 10


# ---------------------------------------------------------------------------
# novel_meal_fields
# ---------------------------------------------------------------------------


class TestNovelMealFields:
    def test_beginner_no_fiber(self):
        rng = random.Random(42)
        results = [novel_meal_fields(BEGINNER, rng, 700) for _ in range(100)]
        assert all(r == {} for r in results)

    def test_intermediate_no_fiber(self):
        rng = random.Random(42)
        results = [novel_meal_fields(INTERMEDIATE, rng, 700) for _ in range(100)]
        assert all(r == {} for r in results)

    def test_advanced_has_fiber(self):
        rng = random.Random(42)
        results = [novel_meal_fields(ADVANCED, rng, 700) for _ in range(100)]
        fiber_count = sum(1 for r in results if "fiber_g" in r)
        assert fiber_count > 0
        for r in results:
            if "fiber_g" in r:
                assert r["fiber_g"] > 0


# ---------------------------------------------------------------------------
# Orphaned event types
# ---------------------------------------------------------------------------


class TestGenerateSupplements:
    def test_beginner_no_supplements(self):
        state = AthleteState.from_profile(BEGINNER)
        rng = random.Random(42)
        assert generate_supplements(BEGINNER, state, rng, 0) == []

    def test_intermediate_two_supplements(self):
        state = AthleteState.from_profile(INTERMEDIATE)
        rng = random.Random(42)
        events = generate_supplements(INTERMEDIATE, state, rng, 0)
        assert len(events) == 2
        for e in events:
            assert e["event_type"] == "supplement.logged"
            assert "name" in e["data"]
            assert "dose_mg" in e["data"]
            assert "timing" in e["data"]

    def test_advanced_four_supplements(self):
        state = AthleteState.from_profile(ADVANCED)
        rng = random.Random(42)
        events = generate_supplements(ADVANCED, state, rng, 0)
        assert len(events) == 4

    def test_idempotency_keys_unique(self):
        state = AthleteState.from_profile(ADVANCED)
        rng = random.Random(42)
        events = generate_supplements(ADVANCED, state, rng, 5)
        keys = [e["idempotency_key"] for e in events]
        assert len(keys) == len(set(keys))


class TestGenerateCardio:
    def test_beginner_no_cardio(self):
        state = AthleteState.from_profile(BEGINNER)
        rng = random.Random(42)
        assert generate_cardio(BEGINNER, state, rng, 0) == []

    def test_intermediate_no_cardio(self):
        state = AthleteState.from_profile(INTERMEDIATE)
        rng = random.Random(42)
        assert generate_cardio(INTERMEDIATE, state, rng, 0) == []

    def test_advanced_sometimes_cardio(self):
        state = AthleteState.from_profile(ADVANCED)
        rng = random.Random(42)
        results = [generate_cardio(ADVANCED, state, rng, i) for i in range(100)]
        cardio_count = sum(1 for r in results if r)
        assert cardio_count > 0  # 40% probability

    def test_cardio_event_format(self):
        state = AthleteState.from_profile(ADVANCED)
        # Find a seed that produces cardio
        for seed in range(100):
            rng = random.Random(seed)
            events = generate_cardio(ADVANCED, state, rng, 0)
            if events:
                e = events[0]
                assert e["event_type"] == "cardio.logged"
                assert e["data"]["type"] in ("running", "cycling", "rowing")
                assert e["data"]["duration_minutes"] > 0
                assert 100 < e["data"]["avg_heart_rate"] < 200
                return
        raise AssertionError("No cardio generated in 100 seeds")


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


class TestEngineWithNovelFields:
    def test_novel_fields_disabled_by_default(self):
        engine = SimulationEngine(BEGINNER)
        events = engine.run(7)
        # No novel fields in default mode
        for e in events:
            if e["event_type"] == "set.logged":
                assert "tempo" not in e["data"]
                assert "bar_speed" not in e["data"]
            if e["event_type"] == "sleep.logged":
                assert "hrv_rmssd" not in e["data"]

    def test_novel_fields_enabled_intermediate(self):
        engine = SimulationEngine(INTERMEDIATE, novel_fields=True)
        events = engine.run(14)

        # Sleep should have HRV (100% for intermediate)
        sleep_events = [e for e in events if e["event_type"] == "sleep.logged"]
        assert len(sleep_events) > 0
        for se in sleep_events:
            assert "hrv_rmssd" in se["data"]
            assert "deep_sleep_pct" in se["data"]

        # Supplements should be generated (intermediate = 2/day)
        supp_events = [e for e in events if e["event_type"] == "supplement.logged"]
        assert len(supp_events) > 0

        # No cardio for intermediate
        cardio_events = [e for e in events if e["event_type"] == "cardio.logged"]
        assert len(cardio_events) == 0

    def test_novel_fields_enabled_advanced(self):
        engine = SimulationEngine(ADVANCED, novel_fields=True)
        events = engine.run(14)

        # Should have cardio events on some rest days
        cardio_events = [e for e in events if e["event_type"] == "cardio.logged"]
        # Advanced trains 5x/week → 2 rest days/week → ~40% chance = ~1-2/week
        # Over 14 days that's ~2-4 events (probabilistic, may be 0)

        # Should have supplements (4/day)
        supp_events = [e for e in events if e["event_type"] == "supplement.logged"]
        assert len(supp_events) >= 14 * 4 * 0.8  # at least most days

        # Meals should have fiber_g sometimes
        meal_events = [e for e in events if e["event_type"] == "meal.logged"]
        fiber_count = sum(1 for m in meal_events if "fiber_g" in m["data"])
        assert fiber_count > 0

    def test_beginner_minimal_novel_fields(self):
        engine = SimulationEngine(BEGINNER, novel_fields=True)
        events = engine.run(14)

        # Beginner: no wearable data
        sleep_events = [e for e in events if e["event_type"] == "sleep.logged"]
        for se in sleep_events:
            assert "hrv_rmssd" not in se["data"]

        # Beginner: no supplements
        supp_events = [e for e in events if e["event_type"] == "supplement.logged"]
        assert len(supp_events) == 0

        # But should have some rest_seconds on working sets
        set_events = [
            e for e in events
            if e["event_type"] == "set.logged" and e["data"].get("set_type") == "working"
        ]
        rest_count = sum(1 for s in set_events if "rest_seconds" in s["data"])
        assert rest_count > 0

    def test_novel_fields_deterministic(self):
        """Same seed should produce same novel fields."""
        e1 = SimulationEngine(INTERMEDIATE, novel_fields=True).run(7)
        e2 = SimulationEngine(INTERMEDIATE, novel_fields=True).run(7)
        assert len(e1) == len(e2)
        for a, b in zip(e1, e2):
            assert a["data"] == b["data"]
            assert a["event_type"] == b["event_type"]

    def test_event_type_summary(self):
        """Novel fields mode should produce new event types."""
        engine = SimulationEngine(ADVANCED, novel_fields=True)
        events = engine.run(30)
        types = {e["event_type"] for e in events}
        assert "supplement.logged" in types
        # cardio.logged is probabilistic, may not appear in 30 days
