"""HTTP resilience + platform adapters, against the fake Twitch/Kick transport."""

import httpx
import pytest

from app.cache.service import CacheService
from app.platforms.errors import (
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    RateLimitedError,
    TemporaryPlatformError,
    UnexpectedResponseError,
)
from app.platforms.http import ClientCredentialsToken, PlatformHttpClient, RetryPolicy, TokenBucket
from app.platforms.kick import KickAdapter
from app.platforms.twitch import TwitchAdapter
from tests.fakes import FakePlatforms, distinctive_image, to_bytes

sleeps: list[float] = []


async def fake_sleep(s: float) -> None:
    sleeps.append(s)


def make(fake: FakePlatforms, *, retries=3, secret="s"):
    client = httpx.AsyncClient(transport=fake.transport())
    cache = CacheService(None)
    tt = ClientCredentialsToken(
        platform="twitch",
        token_url="https://id.twitch.tv/oauth2/token",
        client_id="id",
        client_secret=secret,
        client=client,
    )
    th = PlatformHttpClient(
        platform="twitch",
        base_url="https://api.twitch.tv/helix",
        client=client,
        token=tt,
        auth_headers=lambda t: {"Authorization": f"Bearer {t}", "Client-Id": "id"},
        retry=RetryPolicy(retries, 0.0, 0.0),
        sleep=fake_sleep,
        requests_per_minute=10**6,
    )
    kt = ClientCredentialsToken(
        platform="kick",
        token_url="https://id.kick.com/oauth/token",
        client_id="id",
        client_secret=secret,
        client=client,
    )
    kh = PlatformHttpClient(
        platform="kick",
        base_url="https://api.kick.com/public/v1",
        client=client,
        token=kt,
        retry=RetryPolicy(retries, 0.0, 0.0),
        sleep=fake_sleep,
        requests_per_minute=10**6,
    )
    return TwitchAdapter(th, cache, client), KickAdapter(kh, cache, client), th, tt


def is_users(req: httpx.Request) -> bool:
    return req.url.path == "/helix/users"


@pytest.fixture
def world() -> FakePlatforms:
    f = FakePlatforms()
    f.add_twitch(
        "NikkLive_",
        "NikkLive_",
        description="hi",
        image=to_bytes(distinctive_image(1)),
        game="VALORANT",
        language="de",
        tags=["Deutsch"],
    )
    f.add_kick(
        "nikkilve",
        "NikkiLve",
        description="bio",
        image=to_bytes(distinctive_image(1)),
        category="VALORANT",
        language="German",
    )
    return f


async def test_twitch_lookup_found_and_not_found(world):
    tw, _, _, _ = make(world)
    res = await tw.find_exact_accounts(["nikklive_", "doesnotexist"])
    assert res["doesnotexist"] is None
    p = res["nikklive_"]
    assert p is not None and p.display_name == "NikkLive_" and p.category == "VALORANT" and p.language == "de"


async def test_twitch_invalid_login_short_circuits(world):
    tw, _, _, _ = make(world)
    assert (await tw.find_exact_accounts(["kal-75020yt"]))["kal-75020yt"] is None
    assert world.api_calls("api.twitch.tv") == 0


async def test_twitch_batches_100(world):
    tw, _, _, _ = make(world)
    await tw.find_exact_accounts([f"user{i}" for i in range(250)])
    assert world.calls["api.twitch.tv/helix/users"] == 3


async def test_cache_prevents_repeat_calls_including_negative(world):
    tw, _, _, _ = make(world)
    await tw.find_exact_accounts(["nikklive_", "ghost"])
    before = world.api_calls("api.twitch.tv")
    await tw.find_exact_accounts(["nikklive_", "ghost"])
    assert world.api_calls("api.twitch.tv") == before


async def test_twitch_search_is_candidate_discovery(world):
    tw, _, _, _ = make(world)
    assert "nikklive_" in await tw.search_accounts("nikkilve", 10)


async def test_kick_lookup_with_user_enrichment(world):
    _, kk, _, _ = make(world)
    res = await kk.find_exact_accounts(["nikkilve", "nobody"])
    p = res["nikkilve"]
    assert res["nobody"] is None
    assert p.display_name == "NikkiLve" and p.profile_image_url and p.category == "VALORANT"
    assert KickAdapter.normalize_handle("https://kick.com/NikkiLve") == "nikkilve"
    assert kk.existence_variants("some_user") == ["some_user", "some-user"]


async def test_kick_batches_50(world):
    _, kk, _, _ = make(world)
    await kk.find_exact_accounts([f"slug{i}" for i in range(120)])
    assert world.calls["api.kick.com/public/v1/channels"] == 3


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_transient_errors_are_retried(world, status):
    world.fail(is_users, status=status, times=2)
    tw, _, _, _ = make(world)
    assert (await tw.find_exact_accounts(["nikklive_"]))["nikklive_"] is not None
    assert world.calls["api.twitch.tv/helix/users"] == 3


async def test_persistent_5xx_raises_temporary_not_not_found(world):
    world.fail(is_users, status=503)
    tw, _, _, _ = make(world, retries=2)
    with pytest.raises(TemporaryPlatformError):
        await tw.find_exact_accounts(["nikklive_"])


async def test_timeouts_retried_then_temporary(world):
    world.fail(is_users, exc=httpx.ReadTimeout, times=1)
    tw, _, _, _ = make(world)
    assert (await tw.find_exact_accounts(["nikklive_"]))["nikklive_"] is not None
    world.fail(lambda r: r.url.path == "/public/v1/channels", exc=httpx.ConnectError)
    _, kk, _, _ = make(world, retries=1)
    with pytest.raises(TemporaryPlatformError):
        await kk.find_exact_accounts(["nikkilve"])


async def test_429_honours_retry_after(world):
    sleeps.clear()
    world.fail(is_users, status=429, times=1, headers={"Retry-After": "7"})
    tw, _, _, _ = make(world)
    th_sleep_before = len(sleeps)
    assert (await tw.find_exact_accounts(["nikklive_"]))["nikklive_"] is not None
    assert world.calls["api.twitch.tv/helix/users"] == 2
    assert len(sleeps) > th_sleep_before


async def test_429_exhausted_raises_rate_limited(world):
    world.fail(is_users, status=429)
    tw, _, _, _ = make(world, retries=1)
    with pytest.raises(RateLimitedError):
        await tw.find_exact_accounts(["nikklive_"])


async def test_401_refreshes_token_once_then_auth_error(world):
    world.fail(is_users, status=401, times=1)
    tw, _, _, _ = make(world)
    assert (await tw.find_exact_accounts(["nikklive_"]))["nikklive_"] is not None
    assert world.calls["id.twitch.tv/oauth2/token"] == 2  # refreshed
    world.fail(is_users, status=401)
    tw2, _, _, _ = make(world)
    with pytest.raises(AuthenticationError):
        await tw2.find_exact_accounts(["other"])


async def test_403_is_auth_error_and_400_not_retried(world):
    world.fail(is_users, status=403, times=1)
    tw, _, _, _ = make(world)
    with pytest.raises(AuthenticationError):
        await tw.find_exact_accounts(["a1"])
    world.fail(is_users, status=400, times=1)
    tw, _, _, _ = make(world)
    n = world.calls["api.twitch.tv/helix/users"]
    with pytest.raises(BadRequestError):
        await tw.find_exact_accounts(["a2"])
    assert world.calls["api.twitch.tv/helix/users"] == n + 1


async def test_404_on_lookup_endpoint_is_not_treated_as_missing_account(world):
    world.fail(is_users, status=404)
    tw, _, _, _ = make(world)
    with pytest.raises(UnexpectedResponseError):
        await tw.find_exact_accounts(["nikklive_"])


async def test_bad_credentials_and_missing_credentials(world):
    world.fail(lambda r: r.url.path == "/oauth2/token", status=403)
    tw, _, _, _ = make(world)
    with pytest.raises(AuthenticationError):
        await tw.find_exact_accounts(["x1"])
    tw2, _, _, _ = make(FakePlatforms(), secret="")
    with pytest.raises(ConfigurationError):
        await tw2.find_exact_accounts(["x2"])


async def test_image_download_allowlist(world):
    tw, _, _, _ = make(world)
    url = next(iter(world.images))
    assert await tw.get_image(url.replace("https://static-cdn.jtvnw.net", "https://evil.example.com")) is None
    assert await tw.get_image(url.replace("https://", "http://")) is None


async def test_token_bucket_throttles():
    t = [0.0]
    waited: list[float] = []

    async def sleep(s: float) -> None:
        waited.append(s)
        t[0] += s

    bucket = TokenBucket(60, burst=2, clock=lambda: t[0])
    for _ in range(4):
        await bucket.acquire(sleep)
    assert sum(waited) >= 1.9  # 60/min -> ~1s per request after the burst
