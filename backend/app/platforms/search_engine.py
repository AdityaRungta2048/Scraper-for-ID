"""Search-engine fallback for candidate discovery (Brave Search API — an official, keyed API).

Results are ONLY candidate handles; every one is verified by the identity engine.
Scraping search-engine HTML is deliberately not implemented.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from app.cache.service import CacheService
from app.platforms.base import dedupe
from app.platforms.http import PlatformHttpClient

RESERVED_PATHS = {
    "twitch": {
        "directory",
        "videos",
        "p",
        "settings",
        "search",
        "downloads",
        "jobs",
        "turbo",
        "prime",
        "subscriptions",
        "inventory",
        "wallet",
        "drops",
        "friends",
        "messages",
        "store",
        "login",
        "signup",
        "team",
        "clip",
        "clips",
        "collections",
        "moderator",
        "popout",
        "embed",
        "bits",
        "user",
    },
    "kick": {
        "categories",
        "category",
        "search",
        "browse",
        "video",
        "videos",
        "clips",
        "clip",
        "community-guidelines",
        "terms-of-service",
        "privacy-policy",
        "dmca-policy",
        "following",
        "settings",
        "dashboard",
        "login",
        "signup",
        "subscriptions",
        "api",
        "help",
        "about",
        "careers",
    },
}
DOMAINS = {"twitch": "twitch.tv", "kick": "kick.com"}
HANDLE_RE = {"twitch": re.compile(r"^[a-z0-9_]{1,25}$"), "kick": re.compile(r"^[a-z0-9_-]{1,25}$")}


def handle_from_url(platform: str, url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host != DOMAINS[platform]:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    handle = parts[0].lower()
    if handle in RESERVED_PATHS[platform] or not HANDLE_RE[platform].match(handle):
        return None
    return handle


class SearchEngineProvider(ABC):
    enabled: bool = False

    @abstractmethod
    async def find_handles(self, platform: str, names: list[str], limit: int = 10) -> list[str]: ...


class DisabledSearchEngine(SearchEngineProvider):
    enabled = False

    async def find_handles(self, platform: str, names: list[str], limit: int = 10) -> list[str]:
        return []


class BraveSearchEngine(SearchEngineProvider):
    enabled = True

    def __init__(self, http: PlatformHttpClient, cache: CacheService, cache_ttl: int = 86400) -> None:
        self.http = http
        self.cache = cache
        self.cache_ttl = cache_ttl

    async def find_handles(self, platform: str, names: list[str], limit: int = 10) -> list[str]:
        found: list[str] = []
        for name in dedupe([n.strip() for n in names if n and n.strip()]):
            query = f'site:{DOMAINS[platform]} "{name}"'
            cached = self.cache.get("search_engine", query)
            if cached is None:
                body: Any = await self.http.get_json(
                    "", params={"q": query, "count": 10}, endpoint_category="brave.web_search"
                )
                urls = [
                    r.get("url", "")
                    for r in ((body or {}).get("web") or {}).get("results", [])
                    if isinstance(r, dict)
                ]
                cached = [h for h in (handle_from_url(platform, u) for u in urls) if h]
                self.cache.set("search_engine", query, cached, self.cache_ttl, source="brave")
            found.extend(cached)
        return dedupe(found)[:limit]
