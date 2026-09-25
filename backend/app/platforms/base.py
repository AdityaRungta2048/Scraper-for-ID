"""Platform-independent profile model and adapter interface.

Twitch and Kick implement :class:`PlatformAdapter` so either integration can be
updated independently when a platform changes its API.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class Profile:
    platform: str
    username: str  # canonical handle: Twitch login / Kick slug (lowercase)
    user_id: str | None = None
    display_name: str | None = None
    description: str | None = None
    profile_image_url: str | None = None
    profile_url: str | None = None
    category: str | None = None
    stream_title: str | None = None
    tags: list[str] = field(default_factory=list)
    language: str | None = None
    is_live: bool | None = None
    extra_links: list[str] = field(default_factory=list)  # links from structured profile data
    extra_bio: str | None = None  # bio from public profile enrichment (Kick)
    broadcaster_type: str | None = None
    created_at: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @property
    def all_text(self) -> str:
        return "\n".join(t for t in (self.description, self.extra_bio) if t)


class PlatformAdapter(ABC):
    platform: str = ""
    supports_search: bool = False
    handle_pattern: re.Pattern[str] = re.compile(r"^[a-z0-9_]{1,25}$")
    url_hosts: tuple[str, ...] = ()

    # ----- handle helpers ---------------------------------------------------------
    @classmethod
    def normalize_handle(cls, raw: Any) -> str | None:
        """Turn a cell value / URL / @handle into this platform's canonical handle.

        Returns None if the value can not be a valid handle on this platform.
        """
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        if "/" in text or any(h in text.lower() for h in cls.url_hosts):
            candidate = text if "://" in text else "https://" + text.lstrip("/")
            parsed = urlparse(candidate)
            host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
            if host in cls.url_hosts:
                parts = [p for p in parsed.path.split("/") if p]
                text = parts[0] if parts else ""
            else:
                return None
        text = text.lstrip("@").strip().lower()
        return text if cls.is_valid_handle(text) else None

    @classmethod
    def is_valid_handle(cls, handle: str) -> bool:
        return bool(cls.handle_pattern.match(handle))

    def to_handle(self, variant: str) -> str | None:
        """Convert a generic name variant into a syntactically valid handle, or None."""
        v = variant.strip().lower()
        return v if self.is_valid_handle(v) else None

    def existence_variants(self, handle: str) -> list[str]:
        """Handles that denote *the same account* (platform canonicalisation only)."""
        return [handle]

    def profile_url(self, handle: str) -> str:
        return f"https://{self.url_hosts[0]}/{handle}"

    # ----- data access ------------------------------------------------------------
    @abstractmethod
    async def find_exact_accounts(self, handles: list[str]) -> dict[str, Profile | None]:
        """Exact lookup. Value None == the platform confirmed no such account."""

    async def search_accounts(self, query: str, limit: int = 10) -> list[str]:
        """Candidate discovery via official search. Returns handles (not verified)."""
        return []

    @abstractmethod
    async def get_image(self, url: str) -> bytes | None:
        """Download a public profile image (None if unavailable)."""

    async def find_exact_account(self, handle: str) -> Profile | None:
        return (await self.find_exact_accounts([handle])).get(handle)


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
