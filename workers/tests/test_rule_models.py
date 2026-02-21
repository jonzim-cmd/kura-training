"""Tests for projection rule models (Phase 3, Decision 10)."""

import pytest
from pydantic import ValidationError

from kura_workers.rule_models import (
    CategorizedTrackingRule,
    FieldTrackingRule,
    validate_rule,
)


class TestFieldTrackingRule:
    def test_valid_rule(self):
        rule = FieldTrackingRule(
            name="hrv_tracking",
            type="field_tracking",
            source_events=["sleep.logged"],
            fields=["hrv_rmssd", "deep_sleep_pct"],
        )
        assert rule.name == "hrv_tracking"
        assert rule.type == "field_tracking"
        assert rule.source_events == ["sleep.logged"]
        assert rule.fields == ["hrv_rmssd", "deep_sleep_pct"]

    def test_multiple_source_events(self):
        rule = FieldTrackingRule(
            name="stress_tracking",
            type="field_tracking",
            source_events=["energy.logged", "sleep.logged"],
            fields=["stress_level"],
        )
        assert len(rule.source_events) == 2

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError, match="name must not be empty"):
            FieldTrackingRule(
                name="  ",
                type="field_tracking",
                source_events=["sleep.logged"],
                fields=["hrv_rmssd"],
            )

    def test_empty_fields_rejected(self):
        with pytest.raises(ValidationError, match="fields must not be empty"):
            FieldTrackingRule(
                name="bad_rule",
                type="field_tracking",
                source_events=["sleep.logged"],
                fields=[],
            )

    def test_empty_source_events_rejected(self):
        with pytest.raises(ValidationError, match="source_events must not be empty"):
            FieldTrackingRule(
                name="bad_rule",
                type="field_tracking",
                source_events=[],
                fields=["hrv_rmssd"],
            )

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            FieldTrackingRule(
                name="bad_rule",
                type="field_tracking",
                source_events=["sleep.logged"],
                # fields missing
            )

    def test_wrong_type_literal(self):
        with pytest.raises(ValidationError):
            FieldTrackingRule(
                name="bad_rule",
                type="categorized_tracking",
                source_events=["sleep.logged"],
                fields=["hrv_rmssd"],
            )


class TestCategorizedTrackingRule:
    def test_valid_rule(self):
        rule = CategorizedTrackingRule(
            name="supplement_tracking",
            type="categorized_tracking",
            source_events=["supplement.logged"],
            fields=["name", "dose_mg", "timing"],
            group_by="name",
        )
        assert rule.name == "supplement_tracking"
        assert rule.type == "categorized_tracking"
        assert rule.group_by == "name"

    def test_group_by_must_be_in_fields(self):
        with pytest.raises(ValidationError, match="group_by.*must be one of the declared fields"):
            CategorizedTrackingRule(
                name="bad_rule",
                type="categorized_tracking",
                source_events=["supplement.logged"],
                fields=["dose_mg", "timing"],
                group_by="name",  # not in fields
            )

    def test_missing_group_by(self):
        with pytest.raises(ValidationError):
            CategorizedTrackingRule(
                name="bad_rule",
                type="categorized_tracking",
                source_events=["supplement.logged"],
                fields=["name", "dose_mg"],
                # group_by missing
            )


class TestValidateRule:
    def test_field_tracking_from_dict(self):
        rule = validate_rule({
            "name": "hrv_tracking",
            "type": "field_tracking",
            "source_events": ["sleep.logged"],
            "fields": ["hrv_rmssd", "deep_sleep_pct"],
        })
        assert isinstance(rule, FieldTrackingRule)
        assert rule.name == "hrv_tracking"

    def test_categorized_tracking_from_dict(self):
        rule = validate_rule({
            "name": "supplement_tracking",
            "type": "categorized_tracking",
            "source_events": ["supplement.logged"],
            "fields": ["name", "dose_mg", "timing"],
            "group_by": "name",
        })
        assert isinstance(rule, CategorizedTrackingRule)
        assert rule.group_by == "name"

    def test_rule_type_legacy_alias_is_supported(self):
        rule = validate_rule({
            "name": "hrv_tracking",
            "rule_type": "field_tracking",
            "source_events": ["sleep.logged"],
            "fields": ["hrv_rmssd"],
        })
        assert isinstance(rule, FieldTrackingRule)
        assert rule.type == "field_tracking"

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown rule type"):
            validate_rule({
                "name": "bad",
                "type": "unknown_pattern",
                "source_events": ["x"],
                "fields": ["y"],
            })

    def test_missing_type_raises(self):
        with pytest.raises(ValueError, match="Unknown rule type"):
            validate_rule({
                "name": "bad",
                "source_events": ["x"],
                "fields": ["y"],
            })

    def test_invalid_data_raises_validation_error(self):
        with pytest.raises(ValidationError):
            validate_rule({
                "name": "",
                "type": "field_tracking",
                "source_events": ["sleep.logged"],
                "fields": [],
            })

    def test_extra_fields_ignored(self):
        """Unknown fields in rule data should not cause validation errors."""
        rule = validate_rule({
            "name": "hrv_tracking",
            "type": "field_tracking",
            "source_events": ["sleep.logged"],
            "fields": ["hrv_rmssd"],
            "unknown_extra": "should be ignored",
        })
        assert isinstance(rule, FieldTrackingRule)
