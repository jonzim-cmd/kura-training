"""Static semantic catalog seed data.

This is a pragmatic bootstrap vocabulary for exercise and food resolution.
It can be expanded over time and overridden by user-specific aliases.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CatalogEntry:
    domain: str
    canonical_key: str
    canonical_label: str
    variants: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


EXERCISE_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        domain="exercise",
        canonical_key="barbell_back_squat",
        canonical_label="Barbell Back Squat",
        variants=("back squat", "squat", "kniebeuge", "barbell squat"),
        metadata={"movement_pattern": "squat"},
    ),
    CatalogEntry(
        domain="exercise",
        canonical_key="barbell_bench_press",
        canonical_label="Barbell Bench Press",
        variants=("bench press", "bankdruecken", "bankdrücken", "bb bench"),
        metadata={"movement_pattern": "horizontal_push"},
    ),
    CatalogEntry(
        domain="exercise",
        canonical_key="barbell_deadlift",
        canonical_label="Barbell Deadlift",
        variants=("deadlift", "kreuzheben", "conventional deadlift"),
        metadata={"movement_pattern": "hinge"},
    ),
    CatalogEntry(
        domain="exercise",
        canonical_key="barbell_overhead_press",
        canonical_label="Barbell Overhead Press",
        variants=("overhead press", "shoulder press", "military press", "schulterdruecken"),
        metadata={"movement_pattern": "vertical_push"},
    ),
    CatalogEntry(
        domain="exercise",
        canonical_key="pull_up",
        canonical_label="Pull-Up",
        variants=("pull up", "chin up", "klimmzug"),
        metadata={"movement_pattern": "vertical_pull"},
    ),
    CatalogEntry(
        domain="exercise",
        canonical_key="barbell_row",
        canonical_label="Barbell Row",
        variants=("barbell row", "bent over row", "rudern"),
        metadata={"movement_pattern": "horizontal_pull"},
    ),
)

FOOD_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        domain="food",
        canonical_key="chicken_breast",
        canonical_label="Chicken Breast",
        variants=("chicken breast", "hähnchenbrust", "haehnchenbrust"),
        metadata={"category": "protein"},
    ),
    CatalogEntry(
        domain="food",
        canonical_key="rice_cooked",
        canonical_label="Cooked Rice",
        variants=("rice", "reis", "cooked rice"),
        metadata={"category": "carbohydrate"},
    ),
    CatalogEntry(
        domain="food",
        canonical_key="oats",
        canonical_label="Oats",
        variants=("oats", "haferflocken"),
        metadata={"category": "carbohydrate"},
    ),
    CatalogEntry(
        domain="food",
        canonical_key="egg",
        canonical_label="Egg",
        variants=("egg", "eggs", "ei", "eier"),
        metadata={"category": "protein"},
    ),
    CatalogEntry(
        domain="food",
        canonical_key="whey_protein",
        canonical_label="Whey Protein",
        variants=("whey", "protein shake", "whey protein"),
        metadata={"category": "supplement"},
    ),
)


def all_catalog_entries() -> tuple[CatalogEntry, ...]:
    """Return all global catalog entries."""
    return EXERCISE_CATALOG + FOOD_CATALOG
