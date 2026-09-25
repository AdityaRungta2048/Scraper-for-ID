"""Kick Public API adapter (official API first).

Verified endpoints (see docs/DESIGN.md §B):
  GET /public/v1/channels?slug=…   ≤50 slugs (≤25 chars each) — existence + channel data
  GET /public/v1/users?id=…        user name + profile picture
The official API has no search and no social-link fields.

Optional, disabled by default: ``KICK_PUBLIC_PROFILE_ENRICHMENT`` reads the public
channel JSON kick.com serves to anonymous visitors to obtain bio/social links. It is
undocumented, so the pipeline never depends on it, and any non-JSON / non-200
response (e.g. a bot-protection challenge) simply means "unavailable" — it is never
retried around or bypassed.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.cache.service import CacheService
from app.logging_setup import get_logger
from app.platforms.base import PlatformAdapter, Profile, chunked, dedupe
from app.platforms.errors import UnexpectedResponseError
from app.platforms.http import PlatformHttpClient, download_image

log = get_logger("kick")

_SOCIAL_FIELDS = {
    "instagram": "https://instagram.com/{}",
    "twitter": "https://x.com/{}",
    "youtube": "https://youtube.com/{}",
    "tiktok": "https://tiktok.com/@{}",
    "facebook": "https://facebook.com/{}",
    "discord": "https://discord.gg/{}",
}


class KickAdapter(PlatformAdapter):
    platform = "kick"
    supports_search = False  # the official Kick API has no user/channel search
    handle_pattern = re.compile(r"^[a-z0-9_-]{1,25}$")
    url_hosts = ("kick.com",)
    NS = "kick"

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
        public_enrichment: bool = False,
        public_site_base: str = "https://kick.com",
        public_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.http = http
        self.cache = cache
        self.image_client = image_client
        self.cache_ttl = cache_ttl
        self.negative_ttl = negative_ttl
        self.allowed_image_hosts = allowed_image_hosts or {"files.kick.com", "kick.com"}
        self.image_max_bytes = image_max_bytes
        self.public_enrichment = public_enrichment
        self.public_site_base = public_site_base.rstrip("/")
        self.public_client = public_client

    def profile_url(self, handle: str) -> str:
        return f"https://kick.com/{handle}"

    def to_handle(self, variant: str) -> str | None:
        # Kick slugs are lowercase; usernames containing "_" get "-" in the slug.
        v = variant.strip().lower().replace("_", "-").replace(".", "-").replace(" ", "")
        return v if self.is_valid_handle(v) else None

    def existence_variants(self, handle: str) -> list[str]:
        # "foo_bar" in a sheet refers to the same Kick account as slug "foo-bar".
        return dedupe([handle, handle.replace("_", "-")])

    async def find_exact_accounts(self, handles: list[str]) -> dict[str, Profile | None]:
        result: dict[str, Profile | None] = {}
        to_fetch: list[str] = []
        for h in dedupe([h.lower() for h in handles]):
            if not self.is_valid_handle(h):
                result[h] = None
                continue
            cached = self.cache.get(f"{self.NS}:channel", h)
            if cached is None:
                to_fetch.append(h)
            elif cached.get("_not_found"):
                result[h] = None
            else:
                result[h] = Profile.from_dict(cached)

        fetched: dict[str, Profile] = {}
        for batch in chunked(to_fetch, 50):
            body = await self.http.get_json(
                "channels", params=[("slug", s) for s in batch], endpoint_category="kick.channels"
            )
            for item in _data(body, "channels"):
                slug = str(item.get("slug", "")).lower()
                if not slug:
                    continue
                stream = item.get("stream") or {}
                category = item.get("category") or {}
                fetched[slug] = Profile(
                    platform=self.platform,
                    username=slug,
                    user_id=str(item["broadcaster_user_id"])
                    if item.get("broadcaster_user_id") is not None
                    else None,
                    description=item.get("channel_description") or None,
                    profile_url=self.profile_url(slug),
                    category=(category.get("name") if isinstance(category, dict) else None) or None,
                    stream_title=item.get("stream_title") or None,
                    tags=[str(t) for t in (stream.get("custom_tags") or [])]
                    if isinstance(stream, dict)
                    else [],
                    language=(stream.get("language") if isinstance(stream, dict) else None) or None,
                    is_live=bool(stream.get("is_live")) if isinstance(stream, dict) else None,
                    source="kick-public-v1",
                )

        if fetched:
            await self._enrich_users(list(fetched.values()))
            if self.public_enrichment:
                for fprof in fetched.values():
                    await self._enrich_public_profile(fprof)

        for h in to_fetch:
            prof = fetched.get(h)
            result[h] = prof
            if prof is None:
                self.cache.set(
                    f"{self.NS}:channel", h, {"_not_found": True}, self.negative_ttl, source="kick"
                )
            else:
                self.cache.set(f"{self.NS}:channel", h, prof.to_dict(), self.cache_ttl, source="kick")
        return result

    async def _enrich_users(self, profiles: list[Profile]) -> None:
        by_id = {p.user_id: p for p in profiles if p.user_id}
        for batch in chunked(list(by_id), 50):
            body = await self.http.get_json(
                "users", params=[("id", i) for i in batch], endpoint_category="kick.users"
            )
            for item in _data(body, "users"):
                prof = by_id.get(str(item.get("user_id")))
                if prof is None:
                    continue
                prof.display_name = item.get("name") or None
                prof.profile_image_url = item.get("profile_picture") or None

    async def _enrich_public_profile(self, prof: Profile) -> None:
        """Opt-in: public channel JSON (bio + social handles). Failure is non-fatal."""
        if self.public_client is None:
            return
        url = f"{self.public_site_base}/api/v2/channels/{prof.username}"
        try:
            resp = await self.public_client.get(url, headers={"Accept": "application/json"})
        except (httpx.TimeoutException, httpx.TransportError):
            log.info("kick_public_enrichment_unavailable", slug=prof.username, reason="network")
            return
        if resp.status_code != 200 or "json" not in resp.headers.get("Content-Type", ""):
            log.info("kick_public_enrichment_unavailable", slug=prof.username, status=resp.status_code)
            return
        try:
            body = resp.json()
        except ValueError:
            return
        user = body.get("user") if isinstance(body, dict) else None
        if not isinstance(user, dict):
            return
        prof.extra_bio = user.get("bio") or None
        for field_name, template in _SOCIAL_FIELDS.items():
            value = str(user.get(field_name) or "").strip()
            if not value:
                continue
            prof.extra_links.append(
                value if "://" in value or "." in value else template.format(value.lstrip("@"))
            )
        if not prof.profile_image_url and user.get("profile_pic"):
            prof.profile_image_url = user["profile_pic"]
        prof.source = "kick-public-v1+public-profile"

    async def get_image(self, url: str) -> bytes | None:
        return await download_image(
            self.image_client, url, allowed_hosts=self.allowed_image_hosts, max_bytes=self.image_max_bytes
        )


def _data(body: Any, endpoint: str) -> list[dict[str, Any]]:
    if body is None or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise UnexpectedResponseError(
            f"Kick {endpoint} returned an unexpected response shape", platform="kick"
        )
    return [d for d in body["data"] if isinstance(d, dict)]
