from __future__ import annotations

import pytest

from kura_workers.config import Config


def test_config_from_env_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KURA_WORKER_LISTEN_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL must be set"):
        Config.from_env()


def test_config_from_env_defaults_listen_url_to_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@db/runtime")
    monkeypatch.delenv("KURA_WORKER_LISTEN_DATABASE_URL", raising=False)

    cfg = Config.from_env()
    assert cfg.database_url == "postgresql://app@db/runtime"
    assert cfg.listen_database_url == "postgresql://app@db/runtime"


def test_config_from_env_honors_dedicated_listen_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@db/runtime")
    monkeypatch.setenv("KURA_WORKER_LISTEN_DATABASE_URL", "postgresql://app@db/direct")

    cfg = Config.from_env()
    assert cfg.database_url == "postgresql://app@db/runtime"
    assert cfg.listen_database_url == "postgresql://app@db/direct"
