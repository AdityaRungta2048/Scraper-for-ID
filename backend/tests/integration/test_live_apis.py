"""Live smoke tests against the REAL Twitch/Kick APIs.

Skipped by default (pytest is configured with ``-m 'not live'``). Run with real
credentials in the environment:

    TWITCH_CLIENT_ID=... TWITCH_CLIENT_SECRET=... KICK_CLIENT_ID=... KICK_CLIENT_SECRET=... \
        pytest -m live tests/integration/test_live_apis.py

They only check that the documented endpoints still behave as the adapters expect
(an API change will surface here first). They assert nothing about identity matches.
"""

from __future__ import annotations

import os

import pytest

from app.cache.service import CacheService
from app.config import get_settings
from app.platforms.registry import build_platforms

pytestmark = pytest.mark.live


def _need(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip(f"missing {missing}")


async def test_twitch_user_lookup_and_search_live():
    _need("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET")
    get_settings.cache_clear()
    bundle = build_platforms(get_settings(), CacheService(None))
    try:
        found = await bundle.twitch.find_exact_accounts(["twitch", "this_login_should_not_exist_zzqx"])
        assert found["twitch"] is not None and found["twitch"].user_id
        assert found["this_login_should_not_exist_zzqx"] is None
        assert await bundle.twitch.search_accounts("twitch", 5)
    finally:
        await bundle.aclose()


async def test_kick_channel_lookup_live():
    _need("KICK_CLIENT_ID", "KICK_CLIENT_SECRET")
    get_settings.cache_clear()
    bundle = build_platforms(get_settings(), CacheService(None))
    try:
        found = await bundle.kick.find_exact_accounts(["xqc", "this-slug-should-not-exist-zzqx"])
        assert found["xqc"] is not None and found["xqc"].user_id
        assert found["this-slug-should-not-exist-zzqx"] is None
    finally:
        await bundle.aclose()
