"""IdentityResolver: resolves ONE unique source id into a cacheable, auditable resolution.

    verify source exists → generate candidates → verify identity → aggregate
    (or, if the source does not exist, a strictly deterministic same-name check on
    the target platform, verified only by an explicit back-link)

Manual review verdicts are applied afterwards (``apply_manual_reviews``) so cached
resolutions never bake in human decisions.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.cache.service import CacheService
from app.logging_setup import get_logger
from app.matching.candidates import CandidateGenerator
from app.matching.decision import CandidateEvaluation, Decision, aggregate
from app.matching.names import UsernameMatcher
from app.matching.normalize import normalize_username
from app.matching.signals import Family, Signal, Strength
from app.matching.social import canonical_platform_handle, extract_identities
from app.matching.verifier import IdentityVerifier
from app.platforms.base import PlatformAdapter, Profile, dedupe
from app.services.stores import ProfileStore
from app.version import MATCHING_ENGINE_VERSION

log = get_logger("resolver")

OTHER = {"kick": "twitch", "twitch": "kick"}


def display_id(profile: Profile) -> str:
    """The id written to Excel: Twitch display-cased login (e.g. "NikkLive_"), Kick slug."""
    if (
        profile.platform == "twitch"
        and profile.display_name
        and profile.display_name.lower() == profile.username
    ):
        return profile.display_name
    return profile.username


class IdentityResolver:
    def __init__(
        self,
        adapters: dict[str, PlatformAdapter],
        generator: CandidateGenerator,
        verifier: IdentityVerifier,
        cache: CacheService,
        profile_store: ProfileStore | None = None,
        resolution_ttl: int = 3 * 86400,
    ) -> None:
        self.adapters = adapters
        self.generator = generator
        self.verifier = verifier
        self.cache = cache
        self.profile_store = profile_store
        self.resolution_ttl = resolution_ttl

    def cache_key(self, source_platform: str, key: str, country: str | None) -> str:
        return f"{MATCHING_ENGINE_VERSION}|{source_platform}|{key}|{(country or '').strip().lower()}"

    async def resolve(
        self, source_platform: str, raw_value: str, country: str | None, rejected: set[str] | None = None
    ) -> dict[str, Any]:
        src_adapter = self.adapters[source_platform]
        tgt_platform = OTHER[source_platform]
        tgt_adapter = self.adapters[tgt_platform]
        key = src_adapter.normalize_handle(raw_value)
        cache_key = self.cache_key(source_platform, key or f"invalid:{raw_value.strip().lower()}", country)
        if not rejected:
            cached = self.cache.get("resolution", cache_key)
            if cached is not None:
                return {**cached, "from_cache": True}

        base: dict[str, Any] = {
            "engine_version": MATCHING_ENGINE_VERSION,
            "source_platform": source_platform,
            "target_platform": tgt_platform,
            "source_value": raw_value,
            "source_key": key,
            "country": country,
            "resolved_at": datetime.now(UTC).isoformat(),
        }

        source: Profile | None = None
        if key is not None:
            found = await src_adapter.find_exact_accounts(src_adapter.existence_variants(key))
            source = next((p for p in found.values() if p is not None), None)

        if source is None:
            result = await self._resolve_source_missing(
                base, raw_value, key, src_adapter, tgt_adapter, rejected
            )
        else:
            result = await self._resolve_source_exists(
                base, source, country, src_adapter, tgt_adapter, rejected
            )
        result = apply_match_rules(result)
        if not rejected:
            self.cache.set(
                "resolution",
                cache_key,
                result,
                self.resolution_ttl,
                source="engine",
                source_version=MATCHING_ENGINE_VERSION,
            )
        return result

    async def _resolve_source_exists(
        self,
        base: dict[str, Any],
        source: Profile,
        country: str | None,
        src_adapter: PlatformAdapter,
        tgt_adapter: PlatformAdapter,
        rejected: set[str] | None,
    ) -> dict[str, Any]:
        s_ids = extract_identities(source)
        if self.profile_store:
            self.profile_store.upsert(source, s_ids)
        refs = await self.generator.generate(source, tgt_adapter, s_ids, rejected)
        pairs = [(r.profile, sorted(r.via)) for r in refs if r.profile is not None]
        if self.profile_store:
            for prof, _ in pairs:
                self.profile_store.upsert(prof, extract_identities(prof))
        evals = await self.verifier.verify(source, pairs, country, src_adapter, tgt_adapter)
        agg = aggregate(evals)
        best = agg.best
        ranked = sorted(evals, key=lambda e: (-e.confidence, -e.points))[:10]
        if best is not None and best not in ranked:
            ranked = [best, *ranked[:9]]
        return {
            **base,
            "source_status": "EXISTS",
            "source_profile": source.to_dict(),
            "source_socials": [i.to_dict() for i in s_ids],
            "decision": agg.decision.value,
            "target_status": None,
            "matched_username": best.candidate.username if best and agg.decision == Decision.MATCH else None,
            "matched_id": display_id(best.candidate) if best and agg.decision == Decision.MATCH else None,
            "review_candidate": best.candidate.username if best and agg.decision == Decision.REVIEW else None,
            "confidence": best.confidence if best else None,
            "reason": agg.reason,
            "candidate_count": len(evals),
            "candidates": [e.to_dict() for e in ranked],
        }

    async def _resolve_source_missing(
        self,
        base: dict[str, Any],
        raw_value: str,
        key: str | None,
        src_adapter: PlatformAdapter,
        tgt_adapter: PlatformAdapter,
        rejected: set[str] | None,
    ) -> dict[str, Any]:
        """Source account does not exist. Only a deterministic same-name check is done on the
        target platform; a target account is VERIFIED only if it explicitly links back to the
        exact source id (or a human confirms it later)."""
        name = key or raw_value.strip().lstrip("@").lower()
        handles = dedupe(
            [h for h in (tgt_adapter.to_handle(name), tgt_adapter.to_handle(normalize_username(name))) if h]
        )
        found = await tgt_adapter.find_exact_accounts(handles) if handles else {}
        targets = [p for p in found.values() if p is not None and p.username not in (rejected or set())]
        src_canonical = canonical_platform_handle(src_adapter.platform, name)
        evals: list[CandidateEvaluation] = []
        verified: CandidateEvaluation | None = None
        for prof in targets:
            ids = extract_identities(prof)
            links_back = any(i.kind == src_adapter.platform and i.identity == src_canonical for i in ids)
            name_sig = UsernameMatcher(self.verifier.config).compare(
                Profile(platform=src_adapter.platform, username=name), prof
            )
            link_sig = Signal(
                "cross_link",
                Family.LINK,
                links_back,
                1.0 if links_back else None,
                self.verifier.config.cross_link_weight if links_back else 0.0,
                Strength.STRONG if links_back else Strength.NONE,
                f"{tgt_adapter.platform} profile links to {src_adapter.platform}/{name}"
                if links_back
                else f"{tgt_adapter.platform} profile does not link to {src_adapter.platform}/{name}",
                {"backward": links_back, "forward": False},
            )
            ev = CandidateEvaluation(prof, [link_sig, name_sig], discovered_via=["same_name_check"])
            ev.points = link_sig.points + name_sig.points
            if links_back:
                ev.decision, ev.confidence = Decision.MATCH, 95.0
                ev.reason = (
                    f"Source {src_adapter.platform} account does not exist; the {tgt_adapter.platform} "
                    f"account explicitly links to {src_adapter.platform}/{name}."
                )
                verified = verified or ev
            else:
                ev.decision, ev.confidence = Decision.REVIEW, 0.0
                ev.reason = (
                    f"Source {src_adapter.platform} account does not exist. A same-name "
                    f"{tgt_adapter.platform} account exists but nothing verifies it is the same creator."
                )
            evals.append(ev)
        if verified is not None:
            target_status, decision, best = "VERIFIED", Decision.MATCH, verified
        elif evals:
            target_status, decision, best = "EXISTS_UNVERIFIED", Decision.REVIEW, evals[0]
        else:
            target_status, decision, best = "NOT_FOUND", Decision.NO_MATCH, None
        if self.profile_store:
            for prof in targets:
                self.profile_store.upsert(prof, extract_identities(prof))
        reason = (
            best.reason
            if best
            else (
                f"Source {src_adapter.platform} account does not exist"
                + ("" if key else " (value is not a valid handle for that platform)")
                + f", and no same-name {tgt_adapter.platform} account exists."
            )
        )
        return {
            **base,
            "source_status": "NOT_FOUND",
            "source_profile": None,
            "source_socials": [],
            "decision": decision.value,
            "target_status": target_status,
            "matched_username": best.candidate.username if best and decision == Decision.MATCH else None,
            "matched_id": display_id(best.candidate) if best and decision == Decision.MATCH else None,
            "review_candidate": best.candidate.username if best and decision == Decision.REVIEW else None,
            # an unverified same-name account has no meaningful confidence score
            "confidence": best.confidence if best and target_status == "VERIFIED" else None,
            "reason": reason,
            "candidate_count": len(evals),
            "candidates": [e.to_dict() for e in evals],
        }


def apply_manual_reviews(resolution: dict[str, Any], verdicts: dict[str, str]) -> dict[str, Any]:
    """Apply stored human verdicts {target_username: CONFIRMED|REJECTED} to a resolution."""
    if not verdicts:
        return resolution
    res = dict(resolution)
    cands = [dict(c) for c in res.get("candidates", [])]
    for c in cands:
        verdict = verdicts.get(c["username"])
        if verdict:
            c["manual_verdict"] = verdict
    res["candidates"] = cands
    confirmed = [c for c in cands if c.get("manual_verdict") == "CONFIRMED"]
    if confirmed:
        c = confirmed[0]
        res.update(
            decision="MATCH",
            matched_username=c["username"],
            matched_id=_display_from_candidate(c),
            review_candidate=None,
            confidence=100.0,
            reason=(
                f"Confirmed by manual review ({c['platform']}/{c['username']}). "
                f"Engine said: {c.get('reason', '')}"
            ),
            manual=True,
        )
        if res.get("source_status") == "NOT_FOUND":
            res["target_status"] = "VERIFIED"
        return res
    rejected = {u for u, v in verdicts.items() if v == "REJECTED"}
    if not rejected:
        return res
    live = [c for c in cands if c["username"] not in rejected]
    matches = [c for c in live if c["decision"] == "MATCH"]
    reviews = sorted(
        (c for c in live if c["decision"] == "REVIEW"), key=lambda c: -(c.get("confidence") or 0)
    )
    if len(matches) == 1:
        c = matches[0]
        res.update(
            decision="MATCH",
            matched_username=c["username"],
            matched_id=_display_from_candidate(c),
            review_candidate=None,
            confidence=c["confidence"],
            reason=c["reason"],
        )
    elif len(matches) > 1 or reviews:
        c = matches[0] if matches else reviews[0]
        res.update(
            decision="REVIEW",
            matched_username=None,
            matched_id=None,
            review_candidate=c["username"],
            confidence=c["confidence"],
            reason=c["reason"],
        )
    else:
        res.update(
            decision="NO_MATCH",
            matched_username=None,
            matched_id=None,
            review_candidate=None,
            reason="All remaining candidates were rejected by manual review or did not qualify.",
        )
    if res.get("source_status") == "NOT_FOUND" and res["decision"] != "MATCH":
        # a rejected same-name account still exists; it is simply not this creator
        res["target_status"] = "EXISTS_UNVERIFIED" if cands else "NOT_FOUND"
    res["manual"] = True
    return res


def _display_from_candidate(c: dict[str, Any]) -> str:
    if (
        c.get("platform") == "twitch"
        and c.get("display_name")
        and str(c["display_name"]).lower() == c["username"]
    ):
        return str(c["display_name"])
    return str(c["username"])


# ---------------------------------------------------------------------------------------
# Matching rules (applied last, to every resolution and every platform pair):
#   * source account exists  -> the target account is the same creator ONLY if it has the
#     exact same name, the same profile picture (judged on the picture's subject, not its
#     background), or exactly the same bio; otherwise there is no target account.
#   * source account missing -> a same-name target account is taken as the creator's account.
# No "nearest possible" fallback: a target account is either confirmed or not written at all.
# ---------------------------------------------------------------------------------------
_NAME_SEPARATORS = re.compile(r"[\s._-]+")


def _same_name(a: Any, b: Any) -> bool:
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    return bool(a) and _NAME_SEPARATORS.sub("_", a) == _NAME_SEPARATORS.sub("_", b)


def _same_bio(a: Any, b: Any) -> bool:
    a, b = " ".join(str(a or "").split()), " ".join(str(b or "").split())
    return bool(a) and a == b


def rule_match(source: dict[str, Any], candidate: dict[str, Any]) -> str | None:
    """Which rule makes ``candidate`` the same creator as ``source`` (None = not the same)."""
    # channel names (handles); "foo_bar" and Kick's slug "foo-bar" are the same name
    if _same_name(source.get("username"), candidate.get("username")):
        return "exact same name"
    image = next((s for s in candidate.get("signals") or [] if s.get("name") == "profile_image"), None)
    # STRONG = same picture after border/background trimming and not a generic/default image
    if image and image.get("strength") == Strength.STRONG.value:
        return "same profile picture"
    if _same_bio(source.get("description"), candidate.get("description")) or _same_bio(
        source.get("extra_bio"), candidate.get("description")
    ):
        return "exact same bio"
    return None


def apply_match_rules(resolution: dict[str, Any]) -> dict[str, Any]:
    """Final decision from the matching rules above. A human confirmation always stands;
    incomplete stored evidence (no engine decision) is left for the caller to reject."""
    if resolution.get("decision") is None or (
        resolution.get("manual") and resolution.get("decision") == "MATCH"
    ):
        return resolution
    res = dict(resolution)
    cands = res.get("candidates") or []
    live = [c for c in cands if c.get("username") and c.get("manual_verdict") != "REJECTED"]
    src_pf, tgt_pf = res.get("source_platform"), res.get("target_platform")
    best: dict[str, Any] | None = None
    if res.get("source_status") == "NOT_FOUND":
        best = live[0] if live else None
        why = f"source {src_pf} account does not exist; same-name {tgt_pf} account found"
        res["target_status"] = "VERIFIED" if best else ("EXISTS_UNVERIFIED" if cands else "NOT_FOUND")
    else:
        source = res.get("source_profile") or {}
        hits = [(c, rule_match(source, c)) for c in live]
        order = ["exact same name", "same profile picture", "exact same bio"]
        hits = sorted((h for h in hits if h[1]), key=lambda h: order.index(h[1]))
        if hits:
            best, why = hits[0][0], hits[0][1]
    if best is not None:
        res.update(
            decision="MATCH",
            matched_username=best["username"],
            matched_id=_display_from_candidate(best),
            review_candidate=None,
            reason=f"Matched {tgt_pf}/{best['username']}: {why}.",
        )
    else:
        res.update(
            decision="NO_MATCH",
            matched_username=None,
            matched_id=None,
            review_candidate=None,
            reason=(
                f"No {tgt_pf} account with the exact same name, profile picture or bio."
                if res.get("source_status") == "EXISTS"
                else f"Source {src_pf} account does not exist and no same-name {tgt_pf} account exists."
            ),
        )
    return res
