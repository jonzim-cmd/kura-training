"""Minimal async HTTP health endpoint for Docker healthchecks.

Uses raw asyncio.start_server — no external dependencies.
"""

import asyncio
import json
import logging

import psycopg

from .metrics import get_metrics

logger = logging.getLogger(__name__)

_HTTP_200 = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
_HTTP_503 = "HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n"
_HTTP_404 = "HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n"


async def _check_db(db_url: str) -> str:
    """Try SELECT 1 with a 2s timeout. Returns 'ok' or 'error'."""
    try:
        async with asyncio.timeout(2):
            async with await psycopg.AsyncConnection.connect(
                db_url, autocommit=True
            ) as conn:
                await conn.execute("SELECT 1")
        return "ok"
    except Exception:
        return "error"


async def _handle_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    db_url: str,
) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        request_str = request_line.decode("utf-8", errors="replace")

        # Parse method and path from "GET /health HTTP/1.1\r\n"
        parts = request_str.strip().split()
        path = parts[1] if len(parts) >= 2 else "/"

        if path == "/health":
            db_status = await _check_db(db_url)
            metrics = get_metrics()

            status = "ok" if db_status == "ok" else "degraded"
            body = json.dumps({
                "status": status,
                "uptime_seconds": metrics["uptime_seconds"],
                "db": db_status,
                "metrics": metrics,
            })

            status_line = _HTTP_200 if status == "ok" else _HTTP_503
            response = f"{status_line}Content-Length: {len(body)}\r\n\r\n{body}"
        else:
            body = json.dumps({"error": "not_found"})
            response = f"{_HTTP_404}Content-Length: {len(body)}\r\n\r\n{body}"

        writer.write(response.encode())
        await writer.drain()
    except Exception:
        logger.debug("Health endpoint request error", exc_info=True)
    finally:
        writer.close()
        await writer.wait_closed()


async def start_health_server(port: int, db_url: str) -> asyncio.Server:
    """Start the health HTTP server. Returns the asyncio.Server for lifecycle management."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_request(reader, writer, db_url)

    server = await asyncio.start_server(handler, "0.0.0.0", port)
    logger.info("Health endpoint listening on port %d", port)
    return server
