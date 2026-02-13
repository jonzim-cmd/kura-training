"""Fixtures for adversarial fuzzing tests.

Tests in this package hit the real Rust API — no Python reimplementation
of validation logic. This ensures we test what actually runs in production.

Required environment:
    KURA_API_URL  — API base URL (default: http://localhost:3000)
    KURA_API_KEY  — API key for test user (must exist)

Run:
    uv run pytest tests/fuzzing/ -v
    uv run pytest tests/fuzzing/ -v --hypothesis-seed=42  # reproducible
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
import pytest

from .contracts import CreateEventRequest, EventMetadata

KURA_API_URL = os.environ.get("KURA_API_URL", "http://localhost:3000")
KURA_API_KEY = os.environ.get("KURA_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not KURA_API_KEY,
    reason="KURA_API_KEY not set — fuzzing tests require a running API with auth",
)


class KuraTestClient:
    """Thin HTTP client for the Kura API, used by fuzzing tests."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )

    def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Execute request with automatic retry on 429 rate limit."""
        for attempt in range(5):
            resp = self._client.request(method, url, **kwargs)
            if resp.status_code != 429:
                return resp
            retry_after = float(resp.headers.get("retry-after", "1"))
            time.sleep(max(retry_after, 0.5))
        return resp  # return last 429 if all retries exhausted

    def post_event(self, event: CreateEventRequest) -> tuple[dict[str, Any], int]:
        """POST /v1/events — returns (response_body, status_code)."""
        resp = self._request_with_retry("POST", "/v1/events", json=event.to_dict())
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return body, resp.status_code

    def post_batch(self, events: list[CreateEventRequest]) -> tuple[dict[str, Any], int]:
        """POST /v1/events/batch — returns (response_body, status_code)."""
        payload = {"events": [e.to_dict() for e in events]}
        resp = self._request_with_retry("POST", "/v1/events/batch", json=payload)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return body, resp.status_code

    def post_event_raw(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """POST /v1/events with raw dict payload — for testing malformed requests."""
        resp = self._request_with_retry("POST", "/v1/events", json=payload)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return body, resp.status_code

    def get_projection(self, projection_type: str, key: str) -> tuple[dict[str, Any], int]:
        """GET /v1/projections/{type}/{key}."""
        resp = self._client.get(f"/v1/projections/{projection_type}/{key}")
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return body, resp.status_code

    def get_snapshot(self) -> tuple[dict[str, Any], int]:
        """GET /v1/projections — all projections."""
        resp = self._client.get("/v1/projections")
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return body, resp.status_code

    def close(self) -> None:
        self._client.close()


@pytest.fixture(scope="session")
def api_client() -> KuraTestClient:
    """Session-scoped API client for fuzzing tests.

    Ensures the test user has timezone preference set (required for temporal writes).
    """
    client = KuraTestClient(KURA_API_URL, KURA_API_KEY)
    # Set timezone preference so temporal writes don't get blocked
    _ensure_user_setup(client)
    yield client
    client.close()


def _ensure_user_setup(client: KuraTestClient) -> None:
    """Ensure fuzzing user has required preferences for testing."""
    # Timezone preference (required for temporal event types)
    client.post_event(CreateEventRequest(
        event_type="preference.set",
        data={"key": "timezone", "value": "Europe/Berlin"},
        metadata=EventMetadata(idempotency_key="fuzzing-setup-timezone"),
    ))
    # Close onboarding so planning/coaching events are not blocked
    client.post_event(CreateEventRequest(
        event_type="profile.updated",
        data={"onboarding_phase": "closed"},
        metadata=EventMetadata(idempotency_key="fuzzing-setup-onboarding"),
    ))


@pytest.fixture
def fresh_idempotency_key() -> str:
    """Generate a fresh idempotency key for each test."""
    return str(uuid.uuid4())
