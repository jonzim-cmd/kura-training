#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kura_workers.eval_harness import (
    build_proof_in_production_artifact,
    render_proof_in_production_markdown,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_proof_in_production_artifact",
        description="Generate proof-in-production decision artifacts from a shadow eval report.",
    )
    parser.add_argument(
        "--shadow-report",
        required=True,
        help="Path to the shadow evaluation report JSON.",
    )
    parser.add_argument(
        "--json-output",
        required=True,
        help="Path where the machine-readable artifact JSON will be written.",
    )
    parser.add_argument(
        "--md-output",
        required=True,
        help="Path where the stakeholder-readable Markdown summary will be written.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    shadow_report_path = Path(args.shadow_report)
    json_output_path = Path(args.json_output)
    md_output_path = Path(args.md_output)

    shadow_report = json.loads(shadow_report_path.read_text(encoding="utf-8"))
    artifact = build_proof_in_production_artifact(shadow_report)
    markdown = render_proof_in_production_markdown(artifact)

    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    md_output_path.parent.mkdir(parents=True, exist_ok=True)
    md_output_path.write_text(markdown, encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "json_output": str(json_output_path),
        "md_output": str(md_output_path),
        "decision_status": artifact.get("decision", {}).get("status"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
