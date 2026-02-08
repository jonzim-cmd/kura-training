"""Tests for interview guide structure (Decision 8)."""

from kura_workers.interview_guide import COVERAGE_AREAS, get_interview_guide


class TestInterviewGuideStructure:
    def test_has_required_sections(self):
        guide = get_interview_guide()
        assert "philosophy" in guide
        assert "phases" in guide
        assert "coverage_areas" in guide
        assert "event_conventions" in guide

    def test_philosophy_is_non_empty_list(self):
        guide = get_interview_guide()
        assert isinstance(guide["philosophy"], list)
        assert len(guide["philosophy"]) > 0
        for item in guide["philosophy"]:
            assert isinstance(item, str)
            assert len(item) > 10

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
        convention_types = set(guide["event_conventions"].keys())
        for area in guide["coverage_areas"]:
            for event_type in area["produces"]:
                # program.started is a valid event but not in conventions
                if event_type != "program.started":
                    assert event_type in convention_types, (
                        f"Area '{area['area']}' produces '{event_type}' but no convention defined"
                    )


class TestEventConventions:
    def test_conventions_have_required_fields(self):
        guide = get_interview_guide()
        for event_type, convention in guide["event_conventions"].items():
            assert "description" in convention, f"Convention {event_type} missing 'description'"
            assert "fields" in convention, f"Convention {event_type} missing 'fields'"
            assert "example" in convention, f"Convention {event_type} missing 'example'"

    def test_examples_are_dicts(self):
        guide = get_interview_guide()
        for event_type, convention in guide["event_conventions"].items():
            assert isinstance(convention["example"], dict), (
                f"Convention {event_type} example should be a dict"
            )

    def test_preference_set_convention(self):
        guide = get_interview_guide()
        pref = guide["event_conventions"]["preference.set"]
        assert "key" in pref["example"]
        assert "value" in pref["example"]
        assert "common_keys" in pref

    def test_profile_updated_convention(self):
        guide = get_interview_guide()
        prof = guide["event_conventions"]["profile.updated"]
        assert "experience_level" in prof["fields"]
        assert "training_modality" in prof["fields"]

    def test_injury_reported_convention(self):
        guide = get_interview_guide()
        inj = guide["event_conventions"]["injury.reported"]
        assert "description" in inj["fields"]
        assert "affected_area" in inj["fields"]


class TestCoverageAreasConstant:
    def test_is_non_empty(self):
        assert len(COVERAGE_AREAS) > 0

    def test_no_duplicates(self):
        assert len(COVERAGE_AREAS) == len(set(COVERAGE_AREAS))

    def test_all_strings(self):
        for area in COVERAGE_AREAS:
            assert isinstance(area, str)
