"""Per-run helpers for the matching engine (accounts' social links seen during ONE run).

Nothing here is saved: every processing run starts from nothing, so a profile that changed on a
platform since an earlier run is always read fresh, never taken from an earlier run.
"""

from __future__ import annotations

from app.matching.social import SocialIdentity
from app.platforms.base import Profile


class ProfileStore:
    """Social links of the accounts looked at in this run (for "shared by many accounts" checks)."""

    def __init__(self) -> None:
        self._accounts: dict[tuple[str, str], set[tuple[str, str]]] = {}

    def upsert(self, profile: Profile, identities: list[SocialIdentity]) -> None:
        account = (profile.platform, profile.username)
        for owners in self._accounts.values():
            owners.discard(account)
        for ident in identities:
            self._accounts.setdefault((ident.kind, ident.identity), set()).add(account)

    def social_frequency(self, kind: str, identity: str, exclude: set[tuple[str, str]]) -> int:
        """Number of distinct accounts seen in this run (other than ``exclude``) linking this identity."""
        return len(self._accounts.get((kind, identity), set()) - exclude)
