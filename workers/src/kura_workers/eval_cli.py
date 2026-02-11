"""CLI entry point for offline inference replay evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Sequence

import psycopg

from .eval_harness import run_eval_harness


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kura-eval-harness",
        description="Run offline replay calibration checks for inference projections.",
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="User UUID whose inference projections should be replayed.",
    )
    parser.add_argument(
        "--projection-type",
        action="append",
        choices=("semantic_memory", "strength_inference", "readiness_inference"),
        help="Optional projection type filter (repeatable). Defaults to both.",
    )
    parser.add_argument(
        "--strength-engine",
        default="closed_form",
        choices=("closed_form", "pymc"),
        help="Engine override used during strength replay windows.",
    )
    parser.add_argument(
        "--semantic-top-k",
        default=5,
        type=int,
        help="Candidate cutoff used for semantic ranking metrics.",
    )
    parser.add_argument(
        "--source",
        default="both",
        choices=("projection_history", "event_store", "both"),
        help="Replay source mode.",
    )
    parser.add_argument(
        "--persist",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Persist run + artifacts in inference_eval_runs / inference_eval_artifacts.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set")

    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        await conn.execute("SET ROLE app_worker")
        result = await run_eval_harness(
            conn,
            user_id=args.user_id,
            projection_types=args.projection_type,
            strength_engine=args.strength_engine,
            semantic_top_k=int(args.semantic_top_k),
            source=args.source,
            persist=bool(args.persist),
        )
        if args.persist:
            await conn.commit()
        else:
            await conn.rollback()

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
