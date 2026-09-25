"""TEST/DEMO ONLY — never used in production.

Runs the real FastAPI app with all platform HTTP routed to the in-memory fake
Twitch/Kick world from tests/scenarios.py, so the UI can be exercised end-to-end in
environments without network access to the platforms:

    cd backend && PYTHONPATH=. python -m tests.demo_server   # serves on :8000
"""

from __future__ import annotations

import os

import uvicorn

os.environ.setdefault("TWITCH_CLIENT_ID", "demo")
os.environ.setdefault("TWITCH_CLIENT_SECRET", "demo")
os.environ.setdefault("KICK_CLIENT_ID", "demo")
os.environ.setdefault("KICK_CLIENT_SECRET", "demo")
os.environ.setdefault("BRAVE_SEARCH_API_KEY", "demo")
os.environ.setdefault("DATA_DIR", "./data-demo")
os.environ.setdefault("DATABASE_URL", "sqlite:///./data-demo/demo.db")
os.environ.setdefault("RETRY_BASE_DELAY", "0")

from app.main import create_app
from app.services.context import set_default_transport
from tests.fakes import FakePlatforms
from tests.scenarios import build_kick_world, build_twitch_world


def main() -> None:
    world = FakePlatforms()
    build_kick_world(world)
    build_twitch_world(world)
    world.failures = [f for f in world.failures if f.times < 10**6]  # keep only transient failures
    set_default_transport(world.transport())
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), log_level="warning")


if __name__ == "__main__":
    main()
