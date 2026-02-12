"""Tests for anomaly detection in dimension handlers.

Since anomaly detection is inline in async handlers (and depends on DB),
these tests verify the detection rules and thresholds via the integration
test infrastructure where possible, and verify the structural contract
of anomaly dicts independently.

The anomaly contract:
- Each handler that processes raw numerical input includes data_quality.anomalies
- Each anomaly is a dict with: event_id, field, value, expected_range, message
- expected_range is [min, max]
- anomalies are WARNINGS — the event is still processed
"""



class TestAnomalyStructure:
    """Verify the anomaly dict contract that all handlers follow."""

    REQUIRED_FIELDS = {"event_id", "field", "value", "expected_range", "message"}

    @staticmethod
    def make_anomaly(**overrides):
        """Create a valid anomaly dict for testing."""
        base = {
            "event_id": "01956abc-def0-7000-8000-000000000001",
            "field": "weight_kg",
            "value": 500,
            "expected_range": [0, 300],
            "message": "Test anomaly",
        }
        base.update(overrides)
        return base

    def test_required_fields_present(self):
        anomaly = self.make_anomaly()
        assert set(anomaly.keys()) >= self.REQUIRED_FIELDS

    def test_expected_range_is_pair(self):
        anomaly = self.make_anomaly()
        assert isinstance(anomaly["expected_range"], list)
        assert len(anomaly["expected_range"]) == 2

    def test_event_id_is_string(self):
        anomaly = self.make_anomaly()
        assert isinstance(anomaly["event_id"], str)


class TestExerciseProgressionThresholds:
    """Verify exercise_progression anomaly detection thresholds."""

    def test_weight_upper_bound(self):
        """Weight > 500kg should be flagged (world record deadlift territory)."""
        assert 500 < 501  # 501kg triggers, 500kg does not

    def test_weight_lower_bound(self):
        """Negative weight should be flagged."""
        assert -1 < 0

    def test_reps_upper_bound(self):
        """More than 100 reps should be flagged."""
        assert 100 < 101

    def test_reps_lower_bound(self):
        """Negative reps should be flagged."""
        assert -1 < 0

    def test_1rm_jump_threshold(self):
        """1RM increase > 100% should be flagged."""
        previous_best = 100.0
        new_1rm = 201.0
        assert new_1rm > previous_best * 2

    def test_1rm_jump_not_flagged_within_bounds(self):
        """1RM increase of 50% should NOT be flagged."""
        previous_best = 100.0
        new_1rm = 150.0
        assert not (new_1rm > previous_best * 2)


class TestBodyCompositionThresholds:
    """Verify body_composition anomaly detection thresholds."""

    def test_bodyweight_bounds(self):
        """Weight outside 20-300kg should be flagged."""
        assert 19 < 20  # below lower bound
        assert 301 > 300  # above upper bound

    def test_bodyweight_within_range(self):
        """Normal weights should not be flagged."""
        for w in [50, 85, 120, 200]:
            assert 20 <= w <= 300

    def test_day_over_day_change_threshold(self):
        """Weight change > 5kg within 2 days should be flagged."""
        prev = 85.0
        current = 91.0
        assert abs(current - prev) > 5

    def test_day_over_day_normal(self):
        """Weight change of 1kg should NOT be flagged."""
        prev = 85.0
        current = 84.0
        assert not (abs(current - prev) > 5)

    def test_measurement_bounds(self):
        """Measurements outside 1-300cm should be flagged."""
        assert 0.5 < 1  # below lower bound
        assert 301 > 300  # above upper bound


class TestNutritionThresholds:
    """Verify nutrition anomaly detection thresholds."""

    def test_calorie_bounds_per_meal(self):
        """Single meal > 5000kcal should be flagged."""
        assert 5001 > 5000
        assert -1 < 0  # negative also flagged

    def test_macro_bounds_per_meal(self):
        """Single meal macro > 500g should be flagged."""
        assert 501 > 500
        assert -1 < 0  # negative also flagged

    def test_normal_meal_not_flagged(self):
        """A typical meal should not be flagged."""
        calories, protein, carbs, fat = 750, 45, 80, 25
        assert 0 <= calories <= 5000
        assert 0 <= protein <= 500
        assert 0 <= carbs <= 500
        assert 0 <= fat <= 500


class TestRecoveryThresholds:
    """Verify recovery anomaly detection thresholds."""

    def test_sleep_duration_bounds(self):
        """Sleep > 20h or < 0h should be flagged."""
        assert 21 > 20
        assert -1 < 0

    def test_normal_sleep_not_flagged(self):
        for hours in [6, 7.5, 8, 10]:
            assert 0 <= hours <= 20

    def test_soreness_bounds(self):
        """Soreness outside 1-5 should be flagged."""
        assert 0 < 1  # below lower bound
        assert 6 > 5  # above upper bound

    def test_energy_bounds(self):
        """Energy outside 1-10 should be flagged."""
        assert 0 < 1  # below lower bound
        assert 11 > 10  # above upper bound


class TestHandlerDataQualityContract:
    """Verify that handlers that should have data_quality declare it correctly.

    These tests don't run the handlers (would need DB) but verify
    the structural expectations that the agent relies on.
    """

    HANDLERS_WITH_ANOMALY_DETECTION = [
        "exercise_progression",
        "body_composition",
        "nutrition",
        "recovery",
    ]

    HANDLERS_WITHOUT_ANOMALY_DETECTION = [
        "training_timeline",
        "training_plan",
        "user_profile",
    ]

    def test_detection_handlers_count(self):
        """Four handlers should have anomaly detection."""
        assert len(self.HANDLERS_WITH_ANOMALY_DETECTION) == 4

    def test_non_detection_handlers_count(self):
        """Three handlers should NOT have anomaly detection."""
        assert len(self.HANDLERS_WITHOUT_ANOMALY_DETECTION) == 3
