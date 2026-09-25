from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app import db as dbmod
from app.config import Settings, get_settings
from app.logging_setup import configure_logging
from app.services import context as ctxmod
from tests.fakes import FakePlatforms

configure_logging("WARNING", json=False)

TEST_ENV = {
    "TWITCH_CLIENT_ID": "test-twitch-id",
    "TWITCH_CLIENT_SECRET": "test-twitch-secret",
    "KICK_CLIENT_ID": "test-kick-id",
    "KICK_CLIENT_SECRET": "test-kick-secret",
    "BRAVE_SEARCH_API_KEY": "",
    "RETRY_BASE_DELAY": "0",
    "RETRY_COUNT": "3",
    "QUEUE_BACKEND": "inline",
    "LOG_JSON": "false",
    "LOG_LEVEL": "WARNING",
    "TWITCH_REQUESTS_PER_MINUTE": "100000",
    "KICK_REQUESTS_PER_MINUTE": "100000",
    "SEARCH_REQUESTS_PER_MINUTE": "100000",
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    for k, v in TEST_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    dbmod.init_engine(settings.database_url)
    dbmod.create_all()
    yield settings
    ctxmod.set_default_transport(None)
    get_settings.cache_clear()


@pytest.fixture
def fake(env: Settings) -> FakePlatforms:
    f = FakePlatforms()
    ctxmod.set_default_transport(f.transport())
    return f


@pytest.fixture
def sf(env: Settings):  # type: ignore[no-untyped-def]
    return dbmod.session_factory()
