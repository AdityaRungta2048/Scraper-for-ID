"""CandidateGenerator — DISCOVERY ONLY. It never decides identity.

Stages: explicit links → exact handle → deterministic name variants (batched exact
lookups) → official platform search → optional search-engine fallback → dedupe,
drop manually-rejected pairs, cheap pre-rank, cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.logging_setup import get_logger
from app.matching.names import name_similarity
from app.matching.normalize import (
    basic_casefold,
    fold_accents,
    normalize_username,
    squash_repeats,
    strip_affixes,
    strip_trailing_digits,
)
from app.matching.social import SocialIdentity
from app.platforms.base import PlatformAdapter, Profile, dedupe
from app.platforms.search_engine import DisabledSearchEngine, SearchEngineProvider

log = get_logger("candidates")

SUFFIXES = (
    "tv",
    "ttv",
    "live",
    "yt",
    "gaming",
    "official",
    "oficial",
    "gg",
    "_tv",
    "_live",
    "_yt",
    "_gaming",
    "_official",
    "_oficial",
    "_ttv",
    "_",
    "__",
    "-tv",
    "-live",
    "-",
)
PREFIXES = ("the", "its", "real", "iam", "_", "the_", "its_")


def name_variants(name: str | None) -> list[str]:
    """Deterministic spelling variants of a handle/display name (lowercase)."""
    if not name:
        return []
    base = fold_accents(basic_casefold(name)).lstrip("@").strip()
    forms = {
        base,
        base.replace(" ", ""),
        base.replace(" ", "_"),
        base.replace("-", "_"),
        base.replace("_", "-"),
        base.replace(".", "_"),
        base.replace("-", "").replace("_", "").replace(".", "").replace(" ", ""),
        base.strip("_-"),
    }
    norm = normalize_username(base)
    if not norm:
        return sorted(f for f in forms if f)
    core, _ = strip_affixes(norm)
    cores = {norm, core, strip_trailing_digits(norm), strip_trailing_digits(core), squash_repeats(norm)}
    for c in cores:
        if len(c) < 3:
            continue
        forms.add(c)
        for s in SUFFIXES:
            forms.add(c + s)
        for p in PREFIXES:
            forms.add(p + c)
    return sorted(f for f in forms if f)


@dataclass
class CandidateRef:
    handle: str
    via: set[str] = field(default_factory=set)
    profile: Profile | None = None


class CandidateGenerator:
    def __init__(
        self,
        max_candidates: int = 15,
        search_results_per_query: int = 10,
        search_engine: SearchEngineProvider | None = None,
    ) -> None:
        self.max_candidates = max_candidates
        self.search_results = search_results_per_query
        self.search_engine = search_engine or DisabledSearchEngine()

    async def generate(
        self,
        source: Profile,
        target: PlatformAdapter,
        source_identities: list[SocialIdentity],
        rejected: set[str] | None = None,
    ) -> list[CandidateRef]:
        rejected = rejected or set()
        refs: dict[str, CandidateRef] = {}

        def add(handle: str | None, via: str) -> None:
            if not handle:
                return
            refs.setdefault(handle, CandidateRef(handle)).via.add(via)

        # 1. explicit links from the source profile to the target platform
        for ident in source_identities:
            if ident.kind == target.platform:
                add(target.to_handle(ident.identity), "explicit_link")
        # 2. exact handle, 3. deterministic variants
        add(target.to_handle(source.username), "exact_username")
        for v in name_variants(source.username):
            add(target.to_handle(v), "username_variant")
        if source.display_name and normalize_username(source.display_name) != normalize_username(
            source.username
        ):
            add(target.to_handle(source.display_name), "display_name")
            for v in name_variants(source.display_name):
                add(target.to_handle(v), "display_name_variant")

        found = await target.find_exact_accounts(list(refs))
        for handle, prof in found.items():
            if handle in refs:
                refs[handle].profile = prof
        existing = {h: r for h, r in refs.items() if r.profile is not None}

        # 4. official platform search (discovery only)
        search_handles: dict[str, str] = {}
        if target.supports_search:
            queries = dedupe([q for q in (source.display_name, source.username) if q])
            for q in queries:
                for h in await target.search_accounts(q, self.search_results):
                    search_handles.setdefault(h, "platform_search")

        # 5. search-engine fallback, only when nothing plausible was found yet
        plausible = any(
            "explicit_link" in r.via or self._prerank(source, r.handle) >= 0.8 for r in existing.values()
        ) or any(self._prerank(source, h) >= 0.8 for h in search_handles)
        if self.search_engine.enabled and not plausible:
            names = dedupe([n for n in (source.display_name, source.username) if n])
            for h in await self.search_engine.find_handles(target.platform, names, self.search_results):
                search_handles.setdefault(h, "search_engine")

        new = [h for h in search_handles if h not in existing]
        if new:
            more = await target.find_exact_accounts(new)
            for h, prof in more.items():
                if prof is not None:
                    existing[h] = CandidateRef(h, {search_handles[h]}, prof)
        for h, via in search_handles.items():
            if h in existing:
                existing[h].via.add(via)

        # 6. drop rejected, rank cheaply, cap (explicit links are always kept)
        candidates = [r for r in existing.values() if r.handle not in rejected]
        candidates.sort(
            key=lambda r: ("explicit_link" not in r.via, -self._prerank(source, r.handle, r.profile))
        )
        linked = [r for r in candidates if "explicit_link" in r.via]
        others = [r for r in candidates if "explicit_link" not in r.via]
        result = linked + others[: max(0, self.max_candidates - len(linked))]
        log.info(
            "candidates_generated",
            source_platform=source.platform,
            source=source.username,
            target_platform=target.platform,
            probed=len(refs),
            search_hits=len(search_handles),
            candidate_count=len(result),
        )
        return result

    @staticmethod
    def _prerank(source: Profile, handle: str, profile: Profile | None = None) -> float:
        names = [n for n in (source.username, source.display_name) if n]
        targets = [handle] + ([profile.display_name] if profile and profile.display_name else [])
        return max((name_similarity(a, b)[0] for a in names for b in targets), default=0.0)
