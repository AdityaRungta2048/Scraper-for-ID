"""IdentityVerifier — decides whether candidates are the same creator as the source.

Cheap signals first (links, socials, names, bio, content, country); perceptual image
analysis only for the most promising candidates.
"""

from __future__ import annotations

from app.matching.config import ScoringConfig
from app.matching.decision import CandidateEvaluation, ConfidenceScorer, Decision, DecisionEngine
from app.matching.image import ImageInfo, ProfileImageMatcher
from app.matching.names import DisplayNameMatcher, UsernameMatcher
from app.matching.signals import Family, Signal, Strength, unavailable
from app.matching.social import FrequencyFn, SocialIdentity, SocialLinkMatcher, extract_identities
from app.matching.text import BioMatcher, ContentMatcher, CountryMatcher, TextEmbedder
from app.platforms.base import PlatformAdapter, Profile
from app.platforms.errors import TemporaryPlatformError

IMAGE_PREFILTER_POINTS = 3.0


class IdentityVerifier:
    def __init__(
        self,
        config: ScoringConfig,
        image_matcher: ProfileImageMatcher | None = None,
        social_frequency: FrequencyFn | None = None,
        text_embedder: TextEmbedder | None = None,
        image_max_candidates: int = 6,
    ) -> None:
        self.config = config
        self.social = SocialLinkMatcher(config, social_frequency, config.generic_image_min_accounts)
        self.username = UsernameMatcher(config)
        self.display = DisplayNameMatcher(config)
        self.bio = BioMatcher(config, text_embedder)
        self.content = ContentMatcher(config)
        self.country = CountryMatcher(config)
        self.image = image_matcher or ProfileImageMatcher(config)
        self.engine = DecisionEngine(config)
        self.scorer = ConfidenceScorer(config)
        self.image_max_candidates = image_max_candidates

    def cheap_signals(
        self,
        source: Profile,
        candidate: Profile,
        country: str | None,
        s_ids: list[SocialIdentity],
    ) -> list[Signal]:
        c_ids = extract_identities(candidate)
        return [
            *self.social.compare(source, candidate, s_ids, c_ids),
            self.username.compare(source, candidate),
            self.display.compare(source, candidate),
            self.bio.compare(source, candidate),
            self.content.compare(source, candidate),
            self.country.compare(country, candidate),
        ]

    async def verify(
        self,
        source: Profile,
        candidates: list[tuple[Profile, list[str]]],
        country: str | None,
        source_adapter: PlatformAdapter,
        target_adapter: PlatformAdapter,
    ) -> list[CandidateEvaluation]:
        s_ids = extract_identities(source)
        evals = [
            CandidateEvaluation(
                candidate=c, signals=self.cheap_signals(source, c, country, s_ids), discovered_via=via
            )
            for c, via in candidates
        ]

        def promising(ev: CandidateEvaluation) -> bool:
            strong = any(s.strength == Strength.STRONG for s in ev.signals)
            return (
                strong or self.scorer.points([s for s in ev.signals if s.available]) >= IMAGE_PREFILTER_POINTS
            )

        ranked = sorted(evals, key=lambda e: -self.scorer.points([s for s in e.signals if s.available]))
        image_targets = [e for e in ranked if promising(e)][: self.image_max_candidates]
        transient_image_failure = False
        source_info: ImageInfo | None = None
        if image_targets:
            try:
                source_info = await self.image.load(source_adapter, source)
            except TemporaryPlatformError:
                transient_image_failure = True
        for ev in evals:
            if ev not in image_targets:
                ev.signals.append(
                    unavailable("profile_image", Family.IMAGE, "skipped: weak candidate (cheap filter)")
                )
                continue
            if source_info is None:
                ev.signals.append(
                    unavailable("profile_image", Family.IMAGE, "source image temporarily unavailable")
                )
                continue
            try:
                cand_info = await self.image.load(target_adapter, ev.candidate)
            except TemporaryPlatformError:
                transient_image_failure = True
                ev.signals.append(
                    unavailable("profile_image", Family.IMAGE, "candidate image temporarily unavailable")
                )
                continue
            ev.signals.append(self.image.compare(source, ev.candidate, source_info, cand_info))

        for ev in evals:
            self.engine.evaluate(ev)
        if transient_image_failure and not any(e.decision == Decision.MATCH for e in evals) and image_targets:
            # Missing evidence due to a transient failure must not become a NO_MATCH/REVIEW result.
            raise TemporaryPlatformError("profile image download failed temporarily; row will be retried")
        return evals
