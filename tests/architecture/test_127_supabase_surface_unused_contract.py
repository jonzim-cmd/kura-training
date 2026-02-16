from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIRS = [
    REPO_ROOT / "web" / "src",
    REPO_ROOT / "api" / "src",
    REPO_ROOT / "workers" / "src",
]
RUNTIME_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".rs", ".py"}
BANNED_SNIPPETS = [
    "@supabase/",
    "createClient(",
    "supabase.co/auth/v1",
    "supabase.co/rest/v1",
    "supabase.co/realtime/v1",
    "supabase.co/storage/v1",
    "supabase.co/graphql/v1",
    "NEXT_PUBLIC_SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
]


def _iter_runtime_files() -> list[Path]:
    files: list[Path] = []
    for root in RUNTIME_DIRS:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in RUNTIME_SUFFIXES:
                files.append(path)
    return files


def test_runtime_code_avoids_direct_supabase_client_surfaces() -> None:
    violations: list[str] = []
    for path in _iter_runtime_files():
        src = path.read_text(encoding="utf-8")
        for snippet in BANNED_SNIPPETS:
            if snippet in src:
                rel = path.relative_to(REPO_ROOT)
                violations.append(f"{rel}: contains `{snippet}`")

    assert not violations, "\n".join(violations)


def test_frontend_package_has_no_supabase_sdk_dependency() -> None:
    package_path = REPO_ROOT / "web" / "package.json"
    package_json = json.loads(package_path.read_text(encoding="utf-8"))
    deps = {
        **package_json.get("dependencies", {}),
        **package_json.get("devDependencies", {}),
    }
    supabase_packages = sorted(name for name in deps if name.startswith("@supabase/"))
    assert not supabase_packages, f"Unexpected Supabase deps: {supabase_packages}"


def test_frontend_api_base_points_to_internal_backend() -> None:
    src = (REPO_ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")

    assert "NEXT_PUBLIC_API_URL" in src
    assert "NEXT_PUBLIC_SUPABASE_URL" not in src
