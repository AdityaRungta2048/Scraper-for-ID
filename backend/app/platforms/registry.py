"""Builds the adapters from settings. Tests inject their own transports."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.cache.service import CacheService
from app.config import Settings
from app.platforms.base import PlatformAdapter
from app.platforms.http import ClientCredentialsToken, PlatformHttpClient, RetryPolicy
from app.platforms.kick import KickAdapter
from app.platforms.search_engine import BraveSearchEngine, DisabledSearchEngine, SearchEngineProvider
from app.platforms.twitch import TwitchAdapter

USER_AGENT = "StreamerIdentityMatcher/1.0 (+https://github.com/)"


@dataclass
class PlatformBundle:
    twitch: TwitchAdapter
    kick: KickAdapter
    search_engine: SearchEngineProvider
    clients: list[httpx.AsyncClient]

    def adapter(self, platform: str) -> PlatformAdapter:
        if platform == "twitch":
            return self.twitch
        if platform == "kick":
            return self.kick
        raise ValueError(f"unknown platform {platform!r}")

    async def aclose(self) -> None:
        for c in self.clients:
            await c.aclose()


def build_platforms(
    settings: Settings,
    cache: CacheService,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PlatformBundle:
    timeout = httpx.Timeout(settings.http_timeout)
    common = {"timeout": timeout, "headers": {"User-Agent": USER_AGENT}, "follow_redirects": False}
    if transport is not None:
        common["transport"] = transport
    api_client = httpx.AsyncClient(**common)  # type: ignore[arg-type]
    image_client = httpx.AsyncClient(**{**common, "follow_redirects": True})  # type: ignore[arg-type]
    retry = RetryPolicy(settings.retry_count, settings.retry_base_delay, settings.retry_max_delay)
    image_hosts = {h.strip().lower() for h in settings.image_allowed_hosts.split(",") if h.strip()}

    twitch_id = settings.twitch_client_id
    twitch_token = ClientCredentialsToken(
        platform="twitch",
        token_url=settings.twitch_token_url,
        client_id=twitch_id,
        client_secret=settings.twitch_client_secret.get_secret_value(),
        client=api_client,
    )
    twitch_http = PlatformHttpClient(
        platform="twitch",
        base_url=settings.twitch_api_base,
        client=api_client,
        token=twitch_token,
        auth_headers=lambda t: {"Authorization": f"Bearer {t}", "Client-Id": twitch_id},
        concurrency=settings.api_concurrency,
        requests_per_minute=settings.twitch_requests_per_minute,
        retry=retry,
    )
    kick_token = ClientCredentialsToken(
        platform="kick",
        token_url=settings.kick_token_url,
        client_id=settings.kick_client_id,
        client_secret=settings.kick_client_secret.get_secret_value(),
        client=api_client,
    )
    kick_http = PlatformHttpClient(
        platform="kick",
        base_url=settings.kick_api_base,
        client=api_client,
        token=kick_token,
        concurrency=settings.api_concurrency,
        requests_per_minute=settings.kick_requests_per_minute,
        retry=retry,
    )
    twitch = TwitchAdapter(
        twitch_http,
        cache,
        image_client,
        cache_ttl=settings.cache_ttl,
        negative_ttl=settings.negative_cache_ttl,
        allowed_image_hosts=image_hosts,
        image_max_bytes=settings.image_max_bytes,
    )
    kick = KickAdapter(
        kick_http,
        cache,
        image_client,
        cache_ttl=settings.cache_ttl,
        negative_ttl=settings.negative_cache_ttl,
        allowed_image_hosts=image_hosts,
        image_max_bytes=settings.image_max_bytes,
        public_enrichment=settings.kick_public_profile_enrichment,
        public_site_base=settings.kick_public_site_base,
        public_client=api_client if settings.kick_public_profile_enrichment else None,
    )
    search: SearchEngineProvider
    if settings.search_engine_configured:
        key = settings.brave_search_api_key.get_secret_value()
        search_http = PlatformHttpClient(
            platform="brave",
            base_url=settings.brave_search_url,
            client=api_client,
            token=_StaticToken(),  # type: ignore[arg-type]
            auth_headers=lambda _t: {"X-Subscription-Token": key},
            concurrency=1,
            requests_per_minute=settings.search_requests_per_minute,
            retry=retry,
        )
        search = BraveSearchEngine(search_http, cache, settings.cache_ttl)
    else:
        search = DisabledSearchEngine()
    return PlatformBundle(twitch=twitch, kick=kick, search_engine=search, clients=[api_client, image_client])


class _StaticToken:
    """Stand-in token provider for APIs that use a static key header."""

    async def get(self) -> str:
        return "static"

    def invalidate(self) -> None:
        return None
