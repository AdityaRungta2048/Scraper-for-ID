"""Twitch Helix adapter (official API only).

Verified endpoints (see docs/DESIGN.md §A):
  GET /helix/users?login=…         ≤100 per call — existence + profile
  GET /helix/channels?broadcaster_id=…  ≤100 — category/title/tags/language
  GET /helix/search/channels?query=…&first=…  — candidate discovery only
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.cache.service import CacheService
from app.platforms.base import BatchLookup, PlatformAdapter, Profile, chunked, dedupe
from app.platforms.errors import UnexpectedResponseError
from app.platforms.http import PlatformHttpClient, download_image

DEFAULT_AVATAR_MARKERS = ("user-default-pictures",)
VARIANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{3,24}$")


class TwitchAdapter(PlatformAdapter):
    platform = "twitch"
    supports_search = True
    handle_pattern = re.compile(r"^[a-z0-9_]{1,25}$")
    url_hosts = ("twitch.tv",)
    NS = "twitch"

    def __init__(
        self,
        http: PlatformHttpClient,
        cache: CacheService,
        image_client: httpx.AsyncClient,
        *,
        cache_ttl: int = 86400,
        negative_ttl: int = 21600,
        allowed_image_hosts: set[str] | None = None,
        image_max_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.http = http
        self.cache = cache
        self.image_client = image_client
        self.cache_ttl = cache_ttl
        self.negative_ttl = negative_ttl
        self.allowed_image_hosts = allowed_image_hosts or {"static-cdn.jtvnw.net"}
        self.image_max_bytes = image_max_bytes

    def profile_url(self, handle: str) -> str:
        return f"https://www.twitch.tv/{handle}"

    def to_handle(self, variant: str) -> str | None:
        # Generated variants must satisfy Twitch's username rules (4-25 chars, no leading "_"):
        # Helix rejects the whole batch with HTTP 400 if one login is invalid.
        v = variant.strip().lower().replace("-", "_").replace(".", "_").replace(" ", "")
        return v if VARIANT_PATTERN.match(v) else None

    async def find_exact_accounts(self, handles: list[str]) -> dict[str, Profile | None]:
        result: dict[str, Profile | None] = {}
        to_fetch: list[str] = []
        for h in dedupe([h.lower() for h in handles]):
            if not self.is_valid_handle(h):
                result[h] = None
                continue
            cached = self.cache.get(f"{self.NS}:user", h)
            if cached is None:
                to_fetch.append(h)
            elif cached.get("_not_found"):
                result[h] = None
            else:
                result[h] = Profile.from_dict(cached)

        async def fetch(batch: list[str]) -> list[dict[str, Any]]:
            body = await self.http.get_json(
                "users", params=[("login", h) for h in batch], endpoint_category="helix.users"
            )
            return _data(body, "helix/users")

        lookup = BatchLookup(fetch, probe_handle="twitch")
        fetched: dict[str, Profile] = {}
        for batch in chunked(to_fetch, 100):
            for item in await lookup.run(batch):
                login = str(item.get("login", "")).lower()
                if not login:
                    continue
                fetched[login] = Profile(
                    platform=self.platform,
                    username=login,
                    user_id=str(item.get("id")) if item.get("id") is not None else None,
                    display_name=item.get("display_name") or None,
                    description=item.get("description") or None,
                    profile_image_url=item.get("profile_image_url") or None,
                    profile_url=self.profile_url(login),
                    broadcaster_type=item.get("broadcaster_type") or None,
                    created_at=item.get("created_at") or None,
                    source="twitch-helix",
                )

        if fetched:
            await self._enrich_channels(list(fetched.values()))
        for h in to_fetch:
            prof = fetched.get(h)
            result[h] = prof
            if prof is None:
                self.cache.set(f"{self.NS}:user", h, {"_not_found": True}, self.negative_ttl, source="helix")
            else:
                self.cache.set(f"{self.NS}:user", h, prof.to_dict(), self.cache_ttl, source="helix")
        return result

    async def _enrich_channels(self, profiles: list[Profile]) -> None:
        by_id = {p.user_id: p for p in profiles if p.user_id}
        for batch in chunked(list(by_id), 100):
            body = await self.http.get_json(
                "channels", params=[("broadcaster_id", i) for i in batch], endpoint_category="helix.channels"
            )
            for item in _data(body, "helix/channels"):
                prof = by_id.get(str(item.get("broadcaster_id")))
                if prof is None:
                    continue
                prof.category = item.get("game_name") or None
                prof.stream_title = item.get("title") or None
                prof.tags = [str(t) for t in (item.get("tags") or [])]
                prof.language = item.get("broadcaster_language") or None

    async def search_accounts(self, query: str, limit: int = 10) -> list[str]:
        q = query.strip()
        if not q:
            return []
        key = f"{q.lower()}|{limit}"
        cached = self.cache.get(f"{self.NS}:search", key)
        if cached is not None:
            return list(cached)
        body = await self.http.get_json(
            "search/channels",
            params={"query": q, "first": max(1, min(limit, 100)), "live_only": "false"},
            endpoint_category="helix.search_channels",
        )
        logins = dedupe(
            [str(i.get("broadcaster_login", "")).lower() for i in _data(body, "helix/search/channels")]
        )
        logins = [x for x in logins if x][:limit]
        self.cache.set(f"{self.NS}:search", key, logins, self.cache_ttl, source="helix")
        return logins

    async def get_image(self, url: str) -> bytes | None:
        return await download_image(
            self.image_client, url, allowed_hosts=self.allowed_image_hosts, max_bytes=self.image_max_bytes
        )

    @staticmethod
    def is_default_avatar(url: str | None) -> bool:
        return not url or any(m in url for m in DEFAULT_AVATAR_MARKERS)


def _data(body: Any, endpoint: str) -> list[dict[str, Any]]:
    """Helix wraps results in {"data": [...]}. A missing body means the endpoint moved:
    raise instead of silently treating the account as nonexistent."""
    if body is None or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise UnexpectedResponseError(
            f"Twitch {endpoint} returned an unexpected response shape", platform="twitch"
        )
    return [d for d in body["data"] if isinstance(d, dict)]
