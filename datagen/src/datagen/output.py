"""Output handlers — JSON file and API injection.

Generators produce events in a flat format:
    {"event_type", "data", "occurred_at", "idempotency_key"}

The Kura API expects:
    {"event_type", "data", "timestamp", "metadata": {"idempotency_key", "source"}}

to_api_format() handles the conversion.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx


def to_api_format(event: dict) -> dict:
    """Convert generator event format to Kura API format."""
    return {
        "timestamp": event["occurred_at"],
        "event_type": event["event_type"],
        "data": event["data"],
        "metadata": {
            "idempotency_key": event["idempotency_key"],
            "source": "datagen",
        },
    }


def write_json(events: list[dict], output_path: str | Path) -> int:
    """Write events to a JSON file.

    Returns the number of events written.
    """
    path = Path(output_path)
    with path.open("w") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)
    return len(events)


def inject_to_api(
    events: list[dict],
    base_url: str,
    api_key: str,
    batch_size: int = 100,
) -> dict:
    """Send events to the Kura API via POST /v1/events/batch.

    Returns summary: {"total": N, "batches": N, "errors": [...]}
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    total = len(events)
    batches_sent = 0
    errors: list[str] = []

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        for i in range(0, total, batch_size):
            batch = [to_api_format(e) for e in events[i : i + batch_size]]
            payload = {"events": batch}

            for attempt in range(5):
                try:
                    resp = client.post("/v1/events/batch", json=payload, headers=headers)
                    if resp.status_code == 429:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    if resp.status_code not in (200, 201):
                        errors.append(
                            f"Batch {batches_sent}: HTTP {resp.status_code} — {resp.text[:200]}"
                        )
                    break
                except httpx.HTTPError as e:
                    if attempt == 4:
                        errors.append(f"Batch {batches_sent}: {e}")
                    time.sleep(1.0)

            batches_sent += 1
            # Small delay between batches to avoid rate limits
            time.sleep(0.1)

    return {
        "total": total,
        "batches": batches_sent,
        "errors": errors,
    }
