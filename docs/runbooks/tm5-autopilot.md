# TM5 Autopilot Runbook

Last updated: 2026-02-12

## Issue Status

- `kura-training-tm5.1`: completed
- `kura-training-tm5.3`: completed
- `kura-training-tm5.2`: completed
- `kura-training-tm5.5`: completed
- `kura-training-tm5.6`: completed
- `kura-training-tm5.7`: completed
- `kura-training-tm5.9`: completed
- `kura-training-tm5.8`: completed (post-launch)

## Last Commit

- `7738786` (`feat(kura-training-tm5.8): add provider connection domain model`)

## Tested Commands

- `cd workers && uv run ruff check src/kura_workers/external_activity_contract.py tests/test_external_activity_contract.py` (green)
- `cd workers && uv run pytest -q tests/test_external_activity_contract.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_identity.py tests/test_external_identity.py` (green)
- `cd workers && uv run pytest -q tests/test_external_identity.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_adapter.py tests/test_external_adapter.py` (green)
- `cd workers && uv run pytest -q tests/test_external_adapter.py tests/test_external_activity_contract.py tests/test_external_identity.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_mapping_matrix.py tests/test_external_mapping_matrix.py` (green)
- `cd workers && uv run pytest -q tests/test_external_mapping_matrix.py tests/test_external_activity_contract.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_import_pipeline.py src/kura_workers/handlers/external_import.py src/kura_workers/event_conventions.py tests/test_external_import_pipeline.py` (green)
- `cd workers && uv run pytest -q tests/test_external_import_pipeline.py tests/test_external_mapping_matrix.py tests/test_external_identity.py tests/test_external_adapter.py tests/test_external_activity_contract.py` (green)
- `cargo test -p kura-api imports:: -- --nocapture` (green; 5 passed, existing dead-code warnings unchanged)
- `cd workers && uv run ruff check src/kura_workers/handlers/training_timeline.py src/kura_workers/handlers/quality_health.py src/kura_workers/handlers/external_import.py tests/test_training_timeline.py tests/test_quality_health.py` (green)
- `cd workers && uv run pytest -q tests/test_training_timeline.py tests/test_quality_health.py tests/test_external_import_pipeline.py` (green)
- `cargo test -p kura-api provider_connections:: -- --nocapture` (green; 4 passed, existing dead-code warnings unchanged)

## Open Risks

- Foreign local change in `workers/tests/test_integration.py` is intentionally ignored per user instruction.
- Import API currently has validator-only Rust unit tests; DB-backed endpoint integration tests are still open.

## Next Step

- TM5 chain is complete; next step is optional DB-backed API integration tests for import/provider endpoints.
