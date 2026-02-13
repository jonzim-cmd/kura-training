#!/usr/bin/env python3
"""Generate reproducible API + worker performance baseline metrics."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from kura_workers.handlers.exercise_progression import update_exercise_progression
from kura_workers.handlers.training_timeline import update_training_timeline


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "reports" / "performance-baseline-latest.json"
DEFAULT_PORT = 3400
REPORT_SCHEMA_VERSION = "performance_baseline.v1"
DATASET_PROFILE = "synthetic_set_logged_v1"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int((len(sorted_values) - 1) * p)))
    return round(sorted_values[rank], 3)


def _summarize(label: str, samples_ms: list[float]) -> dict[str, Any]:
    return {
        "label": label,
        "sample_count": len(samples_ms),
        "p50_ms": _percentile(samples_ms, 0.50),
        "p95_ms": _percentile(samples_ms, 0.95),
        "min_ms": round(min(samples_ms), 3) if samples_ms else 0.0,
        "max_ms": round(max(samples_ms), 3) if samples_ms else 0.0,
        "samples_ms": [round(v, 3) for v in samples_ms],
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None
    except Exception:
        return None


def _http_json(
    *,
    base_url: str,
    path: str,
    method: str = "GET",
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout_seconds: float = 20.0,
    max_rate_limit_retries: int = 5,
) -> tuple[float, int, dict[str, Any]]:
    url = f"{base_url}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url=url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    for attempt in range(max_rate_limit_retries + 1):
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                latency_ms = (time.perf_counter() - started) * 1000.0
                payload = json.loads(raw) if raw else {}
                return latency_ms, int(response.status), payload
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_rate_limit_retries:
                retry_after = exc.headers.get("Retry-After", "").strip()
                try:
                    delay_seconds = float(retry_after)
                except ValueError:
                    delay_seconds = 0.1 * float(attempt + 1)
                time.sleep(max(0.05, delay_seconds))
                continue
            raw = exc.read().decode("utf-8", errors="replace")
            detail = raw.strip() or "<empty>"
            raise RuntimeError(f"{method} {path} failed with status {exc.code}: {detail}") from exc

    raise RuntimeError(f"{method} {path} exhausted retry budget after rate limiting")


def _wait_for_health(base_url: str, timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            _latency, status, _payload = _http_json(base_url=base_url, path="/health")
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"API did not become healthy within {timeout_seconds} seconds")


def _start_api_server(
    database_url: str, port: int, startup_timeout_seconds: float
) -> tuple[subprocess.Popen[bytes], Path]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PORT"] = str(port)
    # Local benchmark runs should stay self-contained even if the attestation
    # secret is not exported in .env on a dev machine.
    env.setdefault("KURA_AGENT_MODEL_ATTESTATION_SECRET", "dev-local-benchmark-secret")
    fd, raw_log_path = tempfile.mkstemp(prefix="kura-api-perf-", suffix=".log")
    os.close(fd)
    log_path = Path(raw_log_path)
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        ["cargo", "run", "-p", "kura-api"],
        cwd=REPO_ROOT,
        env=env,
        stdout=log_handle,
        stderr=log_handle,
    )
    log_handle.close()
    try:
        _wait_for_health(f"http://127.0.0.1:{port}", timeout_seconds=startup_timeout_seconds)
    except Exception as exc:
        process.terminate()
        process.wait(timeout=10)
        log_tail = "<no startup logs>"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = "\n".join(lines[-80:]) if lines else "<empty startup logs>"
        raise RuntimeError(
            "Failed to start API benchmark server. "
            f"See startup logs at {log_path}.\n{log_tail}"
        ) from exc
    return process, log_path


def _register_and_login(base_url: str) -> tuple[str, str]:
    email = f"perf-{uuid.uuid4().hex[:12]}@kura.test"
    password = "PerfBench123!"
    register_body = {
        "email": email,
        "password": password,
        "consent_anonymized_learning": True,
    }
    _latency, status, register_payload = _http_json(
        base_url=base_url,
        path="/v1/auth/register",
        method="POST",
        body=register_body,
    )
    if status != 201:
        raise RuntimeError(f"Unexpected register status: {status}")
    user_id = str(register_payload["user_id"])

    login_body = {"email": email, "password": password}
    _latency, status, login_payload = _http_json(
        base_url=base_url,
        path="/v1/auth/email/login",
        method="POST",
        body=login_body,
    )
    if status != 200:
        raise RuntimeError(f"Unexpected login status: {status}")
    token = str(login_payload["access_token"])
    return user_id, token


async def _seed_worker_dataset(
    *,
    database_url: str,
    user_id: str,
    event_count: int,
    window_days: int,
) -> str:
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        started_at = datetime.now(timezone.utc) - timedelta(days=window_days)
        step_minutes = max(1, (window_days * 24 * 60) // max(1, event_count))
        exercises = ("bench_press", "back_squat", "deadlift", "overhead_press")

        async with conn.cursor() as cur:
            for i in range(event_count):
                event_id = uuid.uuid4()
                timestamp = started_at + timedelta(minutes=i * step_minutes)
                exercise = exercises[i % len(exercises)]
                data = {
                    "exercise_id": exercise,
                    "weight_kg": 60.0 + float(i % 40),
                    "reps": 5 + (i % 6),
                    "rpe": 7.0 + float(i % 3) * 0.5,
                    "timezone": "Europe/Berlin",
                }
                metadata = {
                    "source": "performance_baseline",
                    "agent": "benchmark",
                    "session_id": f"perf-session-{i // 5}",
                    "idempotency_key": f"perf-seed-{i}",
                }
                await cur.execute(
                    """
                    INSERT INTO events (id, user_id, timestamp, event_type, data, metadata)
                    VALUES (%s, %s, %s, 'set.logged', %s, %s)
                    """,
                    (event_id, user_id, timestamp, Json(data), Json(metadata)),
                )

        await conn.commit()

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id
                FROM events
                WHERE user_id = %s
                  AND event_type = 'set.logged'
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuntimeError("Failed to seed benchmark event corpus")
            return str(row["id"])


def _require_positive(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be > 0")


def _require_non_negative(name: str, value: int) -> None:
    if value < 0:
        raise ValueError(f"{name} must be >= 0")


async def _benchmark_worker_handlers(
    *,
    database_url: str,
    user_id: str,
    event_id: str,
    warmup_runs: int,
    sample_runs: int,
) -> dict[str, Any]:
    payload = {
        "user_id": user_id,
        "event_id": event_id,
        "event_type": "set.logged",
    }

    handler_defs = [
        ("worker.update_exercise_progression", update_exercise_progression),
        ("worker.update_training_timeline", update_training_timeline),
    ]

    results: list[dict[str, Any]] = []
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        await conn.execute("SET ROLE app_worker")
        await conn.commit()

        for label, handler in handler_defs:
            samples: list[float] = []
            for idx in range(warmup_runs + sample_runs):
                started = time.perf_counter()
                await handler(conn, payload)
                await conn.commit()
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                if idx >= warmup_runs:
                    samples.append(elapsed_ms)
            results.append(_summarize(label, samples))

    return {"handlers": results}


def _benchmark_api_paths(
    *,
    base_url: str,
    token: str,
    warmup_runs: int,
    sample_runs: int,
    pace_seconds: float,
) -> dict[str, Any]:
    endpoint_definitions = [
        ("POST /v1/events", "POST", "/v1/events"),
        ("GET /v1/projections", "GET", "/v1/projections"),
        ("GET /v1/projections/user_profile/me", "GET", "/v1/projections/user_profile/me"),
    ]

    results: list[dict[str, Any]] = []
    for label, method, path in endpoint_definitions:
        samples: list[float] = []
        for idx in range(warmup_runs + sample_runs):
            body = None
            if method == "POST":
                body = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event_type": "set.logged",
                    "data": {
                        "exercise_id": "bench_press",
                        "weight_kg": 85.0,
                        "reps": 5,
                        "rpe": 8.0,
                        "timezone": "Europe/Berlin",
                    },
                    "metadata": {
                        "source": "performance_baseline",
                        "agent": "benchmark",
                        "idempotency_key": f"api-bench-{uuid.uuid4()}",
                    },
                }

            elapsed_ms, status, _payload = _http_json(
                base_url=base_url,
                path=path,
                method=method,
                token=token,
                body=body,
            )
            if status not in {200, 201}:
                raise RuntimeError(f"{label} returned unexpected status {status}")
            if idx >= warmup_runs:
                samples.append(elapsed_ms)
            if pace_seconds > 0:
                time.sleep(pace_seconds)

        results.append(_summarize(label, samples))

    return {"base_url": base_url, "endpoints": results}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set")

    samples = int(args.samples)
    warmup = int(args.warmup)
    worker_event_count = int(args.worker_event_count)
    worker_window_days = int(args.worker_window_days)
    port = int(args.port)
    startup_timeout_seconds = float(args.startup_timeout_seconds)
    api_pace_ms = float(args.api_pace_ms)

    _require_positive("samples", samples)
    _require_non_negative("warmup", warmup)
    _require_positive("worker_event_count", worker_event_count)
    _require_positive("worker_window_days", worker_window_days)
    _require_positive("port", port)
    if startup_timeout_seconds <= 0:
        raise ValueError("startup_timeout_seconds must be > 0")
    if api_pace_ms < 0:
        raise ValueError("api_pace_ms must be >= 0")

    base_url = f"http://127.0.0.1:{port}"
    api_process, api_start_log = _start_api_server(
        database_url,
        port,
        startup_timeout_seconds=startup_timeout_seconds,
    )
    try:
        user_id, token = _register_and_login(base_url)
        latest_event_id = await _seed_worker_dataset(
            database_url=database_url,
            user_id=user_id,
            event_count=worker_event_count,
            window_days=worker_window_days,
        )
        api_metrics = _benchmark_api_paths(
            base_url=base_url,
            token=token,
            warmup_runs=warmup,
            sample_runs=samples,
            pace_seconds=api_pace_ms / 1000.0,
        )
        worker_metrics = await _benchmark_worker_handlers(
            database_url=database_url,
            user_id=user_id,
            event_id=latest_event_id,
            warmup_runs=warmup,
            sample_runs=samples,
        )
    finally:
        api_process.terminate()
        try:
            api_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api_process.kill()
            api_process.wait(timeout=10)

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_command": (
            "set -a && source .env && set +a && "
            "PYTHONPATH=workers/src uv run --project workers "
            "python scripts/performance_baseline.py"
        ),
        "machine_context": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "git_commit": _git_commit(),
            "api_start_log": str(api_start_log),
        },
        "config": {
            "warmup_runs": warmup,
            "sample_runs": samples,
            "api_port": port,
            "worker_event_count": worker_event_count,
            "worker_window_days": worker_window_days,
            "startup_timeout_seconds": startup_timeout_seconds,
            "api_pace_ms": api_pace_ms,
        },
        "dataset": {
            "profile": DATASET_PROFILE,
            "user_id": user_id,
            "seed_event_id": latest_event_id,
        },
        "api": api_metrics,
        "worker": worker_metrics,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="performance_baseline",
        description="Generate reproducible API + worker performance baseline metrics.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to output JSON artifact.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=15,
        help="Sample runs per benchmark (after warmup).",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="Warmup runs per benchmark.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Port used for temporary API server process.",
    )
    parser.add_argument(
        "--worker-event-count",
        type=int,
        default=500,
        help="Seed event count for worker recompute benchmark.",
    )
    parser.add_argument(
        "--worker-window-days",
        type=int,
        default=90,
        help="Synthetic history window in days for worker benchmark corpus.",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum wait time for local API startup before failing.",
    )
    parser.add_argument(
        "--api-pace-ms",
        type=float,
        default=75.0,
        help="Delay between API benchmark requests to avoid local rate-limit bursts.",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    report = asyncio.run(_run(args))
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(output_path)}, indent=2))


if __name__ == "__main__":
    main()
