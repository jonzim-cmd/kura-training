"""Runner test that enforces tests/architecture within existing worker quality gates."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_architecture_specs_pass() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "workers" / "src")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/architecture"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "Architecture specs failed.\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
