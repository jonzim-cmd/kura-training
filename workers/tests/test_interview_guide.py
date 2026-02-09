"""Tests for interview guide structure (Decision 8)."""

from kura_workers.event_conventions import get_event_conventions
from kura_workers.interview_guide import COVERAGE_AREAS, get_interview_guide


class TestInterviewGuideStructure:
    def test_has_required_sections(self):
        guide = get_interview_guide()
        assert "introduction" in guide
        assert "philosophy" in guide
        assert "phases" in guide
        assert "coverage_areas" in guide

    def test_introduction_has_required_fields(self):
        guide = get_interview_guide()
        intro = guide["introduction"]
        assert "purpose" in intro
        assert "tone" in intro
        assert "orientation" in intro
        assert "example" in intro
        # Tone must explicitly discourage role announcements
        assert "identity" in intro["tone"].lower() or "agent" in intro["tone"].lower()
        # Orientation must mention duration and what will be asked
        assert "long" in intro["orientation"].lower() or "minutes" in intro["orientation"].lower()

    def test_philosophy_is_non_empty_list(self):
        guide = get_interview_guide()
        assert isinstance(guide["philosophy"], list)
        assert len(guide["philosophy"]) > 0
        for item in guide["philosophy"]:
            assert isinstance(item, str)
            assert len(item) > 10

    def test_philosophy_enforces_one_question_rule(self):
        guide = get_interview_guide()
        philosophy_text = " ".join(guide["philosophy"]).lower()
        assert "one question" in philosophy_text, "Philosophy must enforce one-question-per-message rule"

    def test_phases_have_required_fields(self):
        guide = get_interview_guide()
        phases = guide["phases"]
        assert "broad_sweep" in phases
        assert "targeted_depth" in phases
        assert "wrap_up" in phases
        for phase_name, phase in phases.items():
            assert "goal" in phase, f"Phase {phase_name} missing 'goal'"
            assert "rules" in phase, f"Phase {phase_name} missing 'rules'"
            assert "when" in phase, f"Phase {phase_name} missing 'when'"


class TestCoverageAreas:
    def test_coverage_areas_have_required_fields(self):
        guide = get_interview_guide()
        for area in guide["coverage_areas"]:
            assert "area" in area, f"Coverage area missing 'area' field: {area}"
            assert "description" in area, f"Coverage area missing 'description': {area}"
            assert "approach" in area, f"Coverage area missing 'approach': {area}"
            assert "produces" in area, f"Coverage area missing 'produces': {area}"
            assert isinstance(area["produces"], list)
            assert len(area["produces"]) > 0

    def test_coverage_areas_match_constant(self):
        guide = get_interview_guide()
        guide_areas = [a["area"] for a in guide["coverage_areas"]]
        assert guide_areas == COVERAGE_AREAS

    def test_valid_approach_types(self):
        valid_approaches = {"categorical", "narrative", "conversational", "categorical_then_narrative"}
        guide = get_interview_guide()
        for area in guide["coverage_areas"]:
            assert area["approach"] in valid_approaches, (
                f"Invalid approach '{area['approach']}' for area '{area['area']}'"
            )

    def test_all_produced_event_types_have_conventions(self):
        guide = get_interview_guide()
        conventions = get_event_conventions()
        for area in guide["coverage_areas"]:
            for event_type in area["produces"]:
                # program.started is a valid event but not in conventions
                if event_type != "program.started":
                    assert event_type in conventions, (
                        f"Area '{area['area']}' produces '{event_type}' but no convention defined"
                    )


class TestEventConventions:
    def test_conventions_have_required_fields(self):
        conventions = get_event_conventions()
        for event_type, convention in conventions.items():
            assert "description" in convention, f"Convention {event_type} missing 'description'"
            assert "fields" in convention, f"Convention {event_type} missing 'fields'"
            assert "example" in convention, f"Convention {event_type} missing 'example'"

    def test_examples_are_dicts(self):
        conventions = get_event_conventions()
        for event_type, convention in conventions.items():
            assert isinstance(convention["example"], dict), (
                f"Convention {event_type} example should be a dict"
            )

    def test_preference_set_convention(self):
        conventions = get_event_conventions()
        pref = conventions["preference.set"]
        assert "key" in pref["example"]
        assert "value" in pref["example"]
        assert "common_keys" in pref

    def test_profile_updated_convention(self):
        conventions = get_event_conventions()
        prof = conventions["profile.updated"]
        assert "experience_level" in prof["fields"]
        assert "training_modality" in prof["fields"]

    def test_injury_reported_convention(self):
        conventions = get_event_conventions()
        inj = conventions["injury.reported"]
        assert "description" in inj["fields"]
        assert "affected_area" in inj["fields"]

    def test_set_logged_convention(self):
        conventions = get_event_conventions()
        assert "set.logged" in conventions
        sl = conventions["set.logged"]
        assert "exercise" in sl["fields"]
        assert "exercise_id" in sl["fields"]
        assert "weight_kg" in sl["fields"]
        assert "reps" in sl["fields"]
        assert "exercise_id" in sl["example"]
        assert "normalization" in sl

    def test_all_tracking_event_types_documented(self):
        """Ensure all major tracking event types have conventions."""
        conventions = get_event_conventions()
        expected = {
            "set.logged", "exercise.alias_created",
            "bodyweight.logged", "measurement.logged",
            "sleep.logged", "soreness.logged", "energy.logged",
            "meal.logged",
            "training_plan.created", "training_plan.updated", "training_plan.archived",
            "weight_target.set", "sleep_target.set", "nutrition_target.set",
            "profile.updated", "preference.set", "goal.set", "injury.reported",
            "event.retracted",
        }
        assert set(conventions.keys()) == expected

    def test_event_retracted_convention(self):
        """event.retracted has required fields and usage guidance."""
        conventions = get_event_conventions()
        retracted = conventions["event.retracted"]
        assert "retracted_event_id" in retracted["fields"]
        assert "retracted_event_type" in retracted["fields"]
        assert "reason" in retracted["fields"]
        assert "usage" in retracted
        assert "retracted_event_id" in retracted["example"]
        assert "retracted_event_type" in retracted["example"]

    def test_profile_updated_null_semantics(self):
        """profile.updated documents how to clear fields."""
        conventions = get_event_conventions()
        prof = conventions["profile.updated"]
        assert "null_semantics" in prof
        assert "null" in prof["null_semantics"]


class TestCoverageAreasConstant:
    def test_is_non_empty(self):
        assert len(COVERAGE_AREAS) > 0

    def test_no_duplicates(self):
        assert len(COVERAGE_AREAS) == len(set(COVERAGE_AREAS))

    def test_all_strings(self):
        for area in COVERAGE_AREAS:
            assert isinstance(area, str)
