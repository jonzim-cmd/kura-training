from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS_SRC = REPO_ROOT / "workers" / "src"
if str(WORKERS_SRC) not in sys.path:
    sys.path.insert(0, str(WORKERS_SRC))


_RUST_TEST_CACHE: dict[tuple[str, str], subprocess.CompletedProcess[str]] = {}


def assert_rust_package_test_passes(package: str, test_name: str) -> None:
    """Run a single Rust unit test by exact name for a given package."""
    cache_key = (package, test_name)
    cached = _RUST_TEST_CACHE.get(cache_key)
    if cached is None:
        result = subprocess.run(
            [
                "cargo",
                "test",
                "-p",
                package,
                test_name,
                "--",
                "--exact",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        _RUST_TEST_CACHE[cache_key] = result
    else:
        result = cached

    assert result.returncode == 0, (
        f"Rust contract test failed ({package}): {test_name}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def assert_kura_api_test_passes(test_name: str) -> None:
    """Run a single kura-api Rust unit test by exact name."""
    assert_rust_package_test_passes("kura-api", test_name)


def assert_kura_mcp_runtime_test_passes(test_name: str) -> None:
    """Run a single kura-mcp-runtime Rust unit test by exact name."""
    assert_rust_package_test_passes("kura-mcp-runtime", test_name)
