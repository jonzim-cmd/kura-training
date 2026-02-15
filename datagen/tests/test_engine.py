"""Integration tests for the simulation engine."""

from datagen.engine import SimulationEngine
from datagen.presets import BEGINNER, INTERMEDIATE, ADVANCED


class TestEngineBasic:
    def test_generates_events(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=7)
        assert len(events) > 50  # Should generate many events in a week

    def test_event_types_present(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=14)
        types = {e["event_type"] for e in events}
        # Core event types should all appear
        assert "profile.updated" in types
        assert "goal.set" in types
        assert "training_plan.created" in types
        assert "set.logged" in types
        assert "sleep.logged" in types
        assert "energy.logged" in types
        assert "bodyweight.logged" in types
        assert "meal.logged" in types
        assert "exercise.alias_created" in types

    def test_targets_on_day_0(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=1)
        types = {e["event_type"] for e in events}
        assert "weight_target.set" in types
        assert "sleep_target.set" in types
        assert "nutrition_target.set" in types

    def test_all_idempotency_keys_unique(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=30)
        keys = [e["idempotency_key"] for e in events]
        assert len(keys) == len(set(keys)), f"Duplicate keys found: {len(keys)} total, {len(set(keys))} unique"

    def test_all_events_have_required_fields(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=14)
        for event in events:
            assert "event_type" in event, f"Missing event_type: {event}"
            assert "data" in event, f"Missing data: {event}"
            assert "occurred_at" in event, f"Missing occurred_at: {event}"
            assert "idempotency_key" in event, f"Missing idempotency_key: {event}"


class TestDeterminism:
    def test_same_seed_same_events(self):
        events1 = SimulationEngine(INTERMEDIATE).run(days=30)
        events2 = SimulationEngine(INTERMEDIATE).run(days=30)
        assert len(events1) == len(events2)
        assert events1 == events2

    def test_different_seeds_different_events(self):
        events_beginner = SimulationEngine(BEGINNER).run(days=7)
        events_advanced = SimulationEngine(ADVANCED).run(days=7)
        # Different profiles → different events
        assert events_beginner != events_advanced


class TestPlausibility:
    def test_set_weights_in_range(self):
        engine = SimulationEngine(ADVANCED)
        events = engine.run(days=30)
        for event in events:
            if event["event_type"] == "set.logged":
                w = event["data"]["weight_kg"]
                assert 0 <= w <= 500, f"Weight {w} out of plausible range"

    def test_set_reps_in_range(self):
        engine = SimulationEngine(ADVANCED)
        events = engine.run(days=30)
        for event in events:
            if event["event_type"] == "set.logged":
                r = event["data"]["reps"]
                assert 0 <= r <= 100, f"Reps {r} out of plausible range"

    def test_sleep_duration_in_range(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=90)
        for event in events:
            if event["event_type"] == "sleep.logged":
                d = event["data"]["duration_hours"]
                assert 0 < d <= 20, f"Sleep duration {d} out of plausible range"

    def test_energy_in_range(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=90)
        for event in events:
            if event["event_type"] == "energy.logged":
                lvl = event["data"]["level"]
                assert 1 <= lvl <= 10, f"Energy level {lvl} out of range"

    def test_soreness_in_range(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=90)
        for event in events:
            if event["event_type"] == "soreness.logged":
                sev = event["data"]["severity"]
                assert 0 <= sev <= 10, f"Soreness severity {sev} out of range"

    def test_meal_calories_in_range(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=30)
        for event in events:
            if event["event_type"] == "meal.logged":
                cal = event["data"]["calories"]
                assert 0 < cal <= 5000, f"Calories {cal} out of range"

    def test_bodyweight_in_range(self):
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=90)
        for event in events:
            if event["event_type"] == "bodyweight.logged":
                w = event["data"]["weight_kg"]
                assert 20 <= w <= 300, f"Bodyweight {w} out of range"


class TestCrossDomainCorrelation:
    def test_epley_consistency(self):
        """Generated working sets should produce plausible Epley 1RM estimates."""
        engine = SimulationEngine(INTERMEDIATE)
        events = engine.run(days=14)

        for event in events:
            if event["event_type"] == "set.logged" and event["data"]["set_type"] == "working":
                w = event["data"]["weight_kg"]
                r = event["data"]["reps"]
                if w > 0 and r > 0:
                    epley_1rm = w * (1 + r / 30.0)
                    # Epley estimate should be within reasonable range
                    # For intermediate: squat ~120, bench ~90, deadlift ~150
                    assert epley_1rm < 600, f"Epley 1RM {epley_1rm} unreasonably high for {event['data']}"

    def test_90_day_simulation(self):
        """Full 90-day simulation for each profile should complete without errors."""
        for profile in [BEGINNER, INTERMEDIATE, ADVANCED]:
            engine = SimulationEngine(profile)
            events = engine.run(days=90)
            assert len(events) > 500, f"{profile.name}: too few events ({len(events)})"

            # Check event type distribution
            types = {}
            for e in events:
                t = e["event_type"]
                types[t] = types.get(t, 0) + 1

            # Should have reasonable counts
            assert types.get("set.logged", 0) > 100, f"{profile.name}: too few sets"
            assert types.get("sleep.logged", 0) == 90, f"{profile.name}: should have 90 sleep events"
            assert types.get("energy.logged", 0) == 90, f"{profile.name}: should have 90 energy events"
            assert types.get("bodyweight.logged", 0) == 90, f"{profile.name}: should have 90 bodyweight events"
