# Performance Baseline Runbook

Last updated: 2026-02-13

## Purpose

Produce a reproducible baseline for API and worker latency before larger runtime refactors.

## Command

```bash
scripts/run-performance-baseline.sh
```

Optional flags:

```bash
scripts/run-performance-baseline.sh \
  --samples 15 \
  --warmup 3 \
  --worker-event-count 500 \
  --worker-window-days 90 \
  --api-pace-ms 75 \
  --startup-timeout-seconds 180 \
  --output docs/reports/performance-baseline-latest.json
```

## Output Artifact

Default artifact path:

`docs/reports/performance-baseline-latest.json`

Contracted output fields:

- `schema_version`: must be `performance_baseline.v1`
- `generated_at`
- `run_command`
- `machine_context`
- `config`
- `dataset`
- `api.endpoints[*].p50_ms` and `api.endpoints[*].p95_ms`
- `worker.handlers[*].p50_ms` and `worker.handlers[*].p95_ms`

## Notes

- The harness starts a temporary local `kura-api` process.
- The run requires `DATABASE_URL` from `.env`.
- Keep the latest baseline artifact in-repo so follow-up gates can diff against it.

## Regression Gate

Compare candidate metrics with the committed baseline:

```bash
scripts/run-performance-regression-gate.sh \
  --baseline docs/reports/performance-baseline-latest.json \
  --candidate docs/reports/performance-baseline-latest.json \
  --output docs/reports/performance-regression-gate-latest.json
```

Gate report contract:

- `schema_version`: `performance_regression_gate.v1`
- `status`: `pass` or `fail`
- `summary.failed_metric_count`
- `metrics[*].failure_reasons`
- `failure_reasons`
