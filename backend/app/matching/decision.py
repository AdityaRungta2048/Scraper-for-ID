"""ConfidenceScorer + DecisionEngine.

Confidence is a transparent mapping of evidence points:
    confidence = 100 * (1 - exp(-points / confidence_scale))
but the decision is NOT threshold-only. MATCH additionally requires independent
strong evidence plus corroboration, and no conflicts (docs/DESIGN.md §F).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.matching.config import ScoringConfig
from app.matching.signals import Family, Signal, Strength
from app.platforms.base import Profile


class Decision(StrEnum):
    MATCH = "MATCH"
    REVIEW = "REVIEW"
    NO_MATCH = "NO_MATCH"


@dataclass
class CandidateEvaluation:
    candidate: Profile
    signals: list[Signal]
    discovered_via: list[str] = field(default_factory=list)
    points: float = 0.0
    confidence: float = 0.0
    decision: Decision = Decision.NO_MATCH
    reason: str = ""
    gates: dict[str, Any] = field(default_factory=dict)

    def signal(self, name: str) -> Signal | None:
        return next((s for s in self.signals if s.name == name), None)

    def evidence_summary(self) -> dict[str, Any]:
        def score(name: str) -> float | None:
            s = self.signal(name)
            return round(s.score, 4) if s and s.available and s.score is not None else None

        social = self.signal("shared_socials")
        link = self.signal("cross_link")
        img = self.signal("profile_image")
        country = self.signal("country")
        return {
            "username_similarity": score("username"),
            "display_name_similarity": score("display_name"),
            "profile_image_similarity": score("profile_image"),
            "profile_image_generic": bool(img and img.data.get("generic")),
            "explicit_cross_link": bool(link and link.strength == Strength.STRONG),
            "cross_link_conflict": bool(link and link.strength == Strength.CONFLICT),
            "social_link_match": bool(social and social.strength == Strength.STRONG),
            "shared_social_accounts": (social.data.get("shared_unique") if social else []) or [],
            "instagram_match": bool(social and social.data.get("instagram_match")),
            "youtube_match": bool(social and social.data.get("youtube_match")),
            "x_match": bool(social and social.data.get("x_match")),
            "tiktok_match": bool(social and social.data.get("tiktok_match")),
            "bio_similarity": score("bio"),
            "content_similarity": score("content"),
            "country_match": bool(country and country.available and country.points > 0),
        }

    def to_dict(self) -> dict[str, Any]:
        c = self.candidate
        return {
            "platform": c.platform,
            "username": c.username,
            "user_id": c.user_id,
            "display_name": c.display_name,
            "profile_url": c.profile_url,
            "profile_image_url": c.profile_image_url,
            "description": c.description,
            "category": c.category,
            "stream_title": c.stream_title,
            "language": c.language,
            "tags": c.tags,
            "discovered_via": self.discovered_via,
            "points": round(self.points, 2),
            "confidence": round(self.confidence, 1),
            "decision": self.decision.value,
            "reason": self.reason,
            "gates": self.gates,
            "evidence": self.evidence_summary(),
            "signals": [s.to_dict() for s in self.signals],
        }


class ConfidenceScorer:
    def __init__(self, config: ScoringConfig) -> None:
        self.config = config

    def points(self, signals: list[Signal]) -> float:
        name_pos = sum(s.points for s in signals if s.family == Family.NAME and s.points > 0)
        name_neg = sum(s.points for s in signals if s.family == Family.NAME and s.points < 0)
        other = sum(s.points for s in signals if s.family != Family.NAME)
        return min(name_pos, self.config.name_family_cap) + name_neg + other

    def confidence(self, points: float) -> float:
        if points <= 0:
            return 0.0
        return 100.0 * (1.0 - math.exp(-points / self.config.confidence_scale))


class DecisionEngine:
    def __init__(self, config: ScoringConfig) -> None:
        self.config = config
        self.scorer = ConfidenceScorer(config)

    def evaluate(self, ev: CandidateEvaluation) -> CandidateEvaluation:
        c = self.config
        sigs = [s for s in ev.signals if s.available]
        ev.points = self.scorer.points(sigs)
        conf = self.scorer.confidence(ev.points)

        strong = {s.family for s in sigs if s.strength == Strength.STRONG}
        supporting = {
            s.family
            for s in sigs
            if s.strength in (Strength.STRONG, Strength.SUPPORTING) and s.family != Family.COUNTRY
        }
        conflicts = [s for s in sigs if s.strength == Strength.CONFLICT]
        negatives = [s for s in sigs if s.strength == Strength.NEGATIVE]
        link = next((s for s in sigs if s.name == "cross_link"), None)
        mutual_link = bool(link and link.data.get("forward") and link.data.get("backward"))
        corroborated = len(strong) >= 2 or (len(strong) == 1 and len(supporting - strong) >= 1) or mutual_link
        # negative image evidence contradicts a match unless links/socials establish identity
        contradicted = bool(negatives) and not ({Family.LINK, Family.SOCIAL} & strong)

        if not strong:
            conf = min(conf, c.match_threshold - 1.0)  # name/content/country alone can never MATCH

        gates = {
            "strong_families": sorted(f.value for f in strong),
            "supporting_families": sorted(f.value for f in supporting - strong),
            "corroborated": corroborated,
            "conflicts": [s.detail for s in conflicts],
            "negative_evidence": [s.detail for s in negatives],
            "name_only_cap_applied": not strong,
        }
        if conflicts:
            decision = Decision.REVIEW if ({Family.SOCIAL, Family.IMAGE} & strong) else Decision.NO_MATCH
            conf = min(conf, c.match_threshold - 1.0)
        elif strong and conf >= c.match_threshold and corroborated and not contradicted:
            decision = Decision.MATCH
        elif conf >= c.review_threshold or strong:
            decision = Decision.REVIEW
            if contradicted:
                conf = min(conf, c.match_threshold - 1.0)
        else:
            decision = Decision.NO_MATCH
        ev.confidence = round(conf, 1)
        ev.decision = decision
        ev.gates = gates
        ev.reason = self._reason(ev, sigs, strong, corroborated, contradicted, conflicts)
        return ev

    def _reason(
        self,
        ev: CandidateEvaluation,
        sigs: list[Signal],
        strong: set[Family],
        corroborated: bool,
        contradicted: bool,
        conflicts: list[Signal],
    ) -> str:
        pos = sorted((s for s in sigs if s.points > 0), key=lambda s: -s.points)
        neg = [s for s in sigs if s.points < 0 or s.strength in (Strength.CONFLICT, Strength.NEGATIVE)]
        strong_txt = "; ".join(s.detail for s in pos if s.strength == Strength.STRONG)
        support_txt = "; ".join(s.detail for s in pos if s.strength != Strength.STRONG)
        neg_txt = "; ".join(s.detail for s in neg)
        if ev.decision == Decision.MATCH:
            txt = f"Strong cross-platform identity evidence: {strong_txt}."
            if support_txt:
                txt += f" Corroborated by: {support_txt}."
            return txt
        if ev.decision == Decision.REVIEW:
            parts = ["Evidence is insufficient to match safely."]
            if conflicts:
                parts.append(f"Conflicting evidence: {neg_txt}.")
            elif not strong:
                parts.append("Only name/content-level similarity (no independent identity evidence).")
            elif not corroborated:
                parts.append("Strong signal present but not corroborated by an independent signal.")
            elif contradicted:
                parts.append(f"Signals conflict: {neg_txt}.")
            else:
                parts.append("Confidence below the match threshold.")
            if strong_txt:
                parts.append(f"Strong: {strong_txt}.")
            if support_txt:
                parts.append(f"Supporting: {support_txt}.")
            return " ".join(parts)
        txt = "Not the same creator (insufficient evidence)."
        if neg_txt:
            txt += f" Against: {neg_txt}."
        if support_txt:
            txt += f" Weak similarities only: {support_txt}."
        return txt


@dataclass
class AggregateDecision:
    decision: Decision
    best: CandidateEvaluation | None
    reason: str


def aggregate(evaluations: list[CandidateEvaluation]) -> AggregateDecision:
    """Combine per-candidate decisions. Never 'pick the most similar' among several matches."""
    ranked = sorted(evaluations, key=lambda e: (-_rank(e.decision), -e.confidence, -e.points))
    matches = [e for e in ranked if e.decision == Decision.MATCH]
    if len(matches) == 1:
        return AggregateDecision(Decision.MATCH, matches[0], matches[0].reason)
    if len(matches) > 1:
        names = ", ".join(m.candidate.username for m in matches)
        for m in matches:
            m.gates["ambiguous_with"] = [x.candidate.username for x in matches if x is not m]
        return AggregateDecision(
            Decision.REVIEW,
            matches[0],
            f"Ambiguous: several candidates satisfy the match criteria ({names}); human review required.",
        )
    reviews = [e for e in ranked if e.decision == Decision.REVIEW]
    if reviews:
        return AggregateDecision(Decision.REVIEW, reviews[0], reviews[0].reason)
    if ranked:
        return AggregateDecision(
            Decision.NO_MATCH,
            ranked[0],
            f"No candidate reached the evidence requirements. Best: {ranked[0].reason}",
        )
    return AggregateDecision(Decision.NO_MATCH, None, "No candidate accounts found on the target platform.")


def _rank(d: Decision) -> int:
    return {Decision.MATCH: 2, Decision.REVIEW: 1, Decision.NO_MATCH: 0}[d]
