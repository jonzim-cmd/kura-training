"""CLI interface for the Kura synthetic data generator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from datagen.engine import SimulationEngine
from datagen.models import AthleteProfile
from datagen.output import inject_to_api, write_json
from datagen.presets import PRESETS


@click.group()
def main():
    """Kura synthetic training data generator."""


@main.command()
@click.option(
    "--profile", "profile_name",
    type=click.Choice(list(PRESETS.keys())),
    help="Use a preset athlete profile.",
)
@click.option(
    "--profile-file",
    type=click.Path(exists=True, path_type=Path),
    help="Load a custom profile from JSON file.",
)
@click.option("--days", type=int, default=90, show_default=True, help="Number of days to simulate.")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Write events to JSON file.")
@click.option("--api", type=str, help="API base URL for injection (e.g. http://localhost:3000).")
@click.option("--api-key", type=str, help="API key for authentication (kura_sk_...).")
def generate(
    profile_name: str | None,
    profile_file: Path | None,
    days: int,
    output: Path | None,
    api: str | None,
    api_key: str | None,
):
    """Generate synthetic training data."""
    if profile_name and profile_file:
        click.echo("Error: Specify either --profile or --profile-file, not both.", err=True)
        sys.exit(1)

    if not profile_name and not profile_file:
        click.echo("Error: Specify --profile or --profile-file.", err=True)
        sys.exit(1)

    if api and not api_key:
        click.echo("Error: --api-key is required when using --api.", err=True)
        sys.exit(1)

    if not output and not api:
        click.echo("Error: Specify --output and/or --api.", err=True)
        sys.exit(1)

    # Load profile
    if profile_file:
        with profile_file.open() as f:
            data = json.load(f)
        profile = AthleteProfile(**data)
    else:
        profile = PRESETS[profile_name]

    click.echo(f"Simulating {profile.name} ({profile.experience_level}) for {days} days...")

    engine = SimulationEngine(profile)
    events = engine.run(days)

    click.echo(f"Generated {len(events)} events.")

    # Event type summary
    type_counts: dict[str, int] = {}
    for e in events:
        t = e["event_type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, count in sorted(type_counts.items()):
        click.echo(f"  {t}: {count}")

    if output:
        n = write_json(events, output)
        click.echo(f"Wrote {n} events to {output}")

    if api:
        click.echo(f"Injecting into {api}...")
        result = inject_to_api(events, api, api_key)
        click.echo(f"Sent {result['total']} events in {result['batches']} batches.")
        if result["errors"]:
            click.echo(f"Errors ({len(result['errors'])}):", err=True)
            for err in result["errors"]:
                click.echo(f"  {err}", err=True)
            sys.exit(1)


@main.command("list-profiles")
def list_profiles():
    """List available preset profiles."""
    for name, profile in PRESETS.items():
        click.echo(f"{name}:")
        click.echo(f"  Experience: {profile.experience_level}")
        click.echo(f"  Bodyweight: {profile.bodyweight_kg} kg")
        click.echo(f"  Training: {profile.training_days_per_week}x/week")
        click.echo(f"  Squat 1RM: {profile.squat_1rm_kg} kg")
        click.echo(f"  Bench 1RM: {profile.bench_1rm_kg} kg")
        click.echo(f"  Deadlift 1RM: {profile.deadlift_1rm_kg} kg")
        click.echo(f"  OHP 1RM: {profile.ohp_1rm_kg} kg")
        click.echo(f"  Calories: {profile.calorie_target}")
        click.echo(f"  Progression: +{profile.progression_rate * 100:.1f}%/week")
        click.echo()
