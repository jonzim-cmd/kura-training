"""Contract consistency tests between guide, conventions, and handlers."""

# Import handlers so decorator-based registration is complete.
import kura_workers.handlers  # noqa: F401
from kura_workers.event_conventions import get_event_conventions
from kura_workers.handlers.user_profile import _compute_interview_coverage
from kura_workers.interview_guide import COVERAGE_AREAS, get_interview_guide
from kura_workers.registry import get_projection_handlers


def _coverage_status_for(area: str, coverage: list[dict]) -> str:
    for item in coverage:
        if item["area"] == area:
            return item["status"]
    raise AssertionError(f"Coverage area {area!r} missing from output")


def test_interview_guide_areas_match_constant():
    guide_areas = [a["area"] for a in get_interview_guide()["coverage_areas"]]
    assert guide_areas == COVERAGE_AREAS


def test_every_interview_output_event_has_convention():
    conventions = set(get_event_conventions().keys())
    missing: list[str] = []

    for area in get_interview_guide()["coverage_areas"]:
        for event_type in area.get("produces", []):
            if event_type not in conventions:
                missing.append(f"{area['area']} -> {event_type}")

    assert not missing, f"Missing event_conventions for: {missing}"


def test_interview_output_events_update_user_profile_projection():
    missing: list[str] = []

    for area in get_interview_guide()["coverage_areas"]:
        for event_type in area.get("produces", []):
            handlers = get_projection_handlers(event_type)
            if not any(h.__name__ == "update_user_profile" for h in handlers):
                missing.append(f"{area['area']} -> {event_type}")

    assert not missing, f"Events not wired to user_profile: {missing}"


def test_every_coverage_area_can_be_marked_covered():
    # Base context with nothing covered.
    aliases: dict[str, dict[str, str]] = {}
    preferences: dict[str, object] = {}
    goals: list[dict[str, object]] = []
    profile_data: dict[str, object] = {}
    injuries: list[dict[str, object]] = []

    scenarios = {
        "training_background": (
            aliases,
            preferences,
            goals,
            {**profile_data, "training_modality": "strength"},
            injuries,
        ),
        "goals": (
            aliases,
            preferences,
            [{"goal_type": "strength"}],
            profile_data,
            injuries,
        ),
        "exercise_vocabulary": (
            {
                "sq": {"target": "barbell_back_squat", "confidence": "confirmed"},
                "bp": {"target": "barbell_bench_press", "confidence": "confirmed"},
                "dl": {"target": "barbell_deadlift", "confidence": "confirmed"},
            },
            preferences,
            goals,
            profile_data,
            injuries,
        ),
        "unit_preferences": (
            aliases,
            {**preferences, "unit_system": "metric"},
            goals,
            profile_data,
            injuries,
        ),
        "injuries": (
            aliases,
            preferences,
            goals,
            profile_data,
            [{"description": "knee pain"}],
        ),
        "equipment": (
            aliases,
            preferences,
            goals,
            {**profile_data, "available_equipment": ["barbell", "rack"]},
            injuries,
        ),
        "schedule": (
            aliases,
            preferences,
            goals,
            {**profile_data, "training_frequency_per_week": 4},
            injuries,
        ),
        "nutrition_interest": (
            aliases,
            {**preferences, "nutrition_tracking": "active"},
            goals,
            profile_data,
            injuries,
        ),
        "current_program": (
            aliases,
            preferences,
            goals,
            {**profile_data, "current_program": "5/3/1"},
            injuries,
        ),
        "communication_preferences": (
            aliases,
            preferences,
            goals,
            {**profile_data, "communication_style": "short and direct"},
            injuries,
        ),
    }

    for area, params in scenarios.items():
        coverage = _compute_interview_coverage(*params)
        assert _coverage_status_for(area, coverage) == "covered"
