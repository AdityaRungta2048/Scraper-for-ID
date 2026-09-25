"""UsernameMatcher and DisplayNameMatcher.

A name match is never proof of identity: both matchers only emit SUPPORTING/WEAK
evidence, scaled by how distinctive the name is.
"""

from __future__ import annotations

from rapidfuzz.distance import JaroWinkler, Levenshtein

from app.matching.config import ScoringConfig
from app.matching.normalize import (
    basic_casefold,
    name_distinctiveness,
    normalize_username,
    squash_repeats,
    strip_affixes,
    strip_trailing_digits,
)
from app.matching.signals import Family, Signal, Strength, unavailable
from app.platforms.base import Profile


def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    lev = Levenshtein.normalized_similarity(a, b)
    jw = JaroWinkler.similarity(a, b)
    ba, bb = _bigrams(a), _bigrams(b)
    jac = len(ba & bb) / len(ba | bb) if ba | bb else 0.0
    return 0.45 * lev + 0.40 * jw + 0.15 * jac


def name_similarity(a: str | None, b: str | None) -> tuple[float, str]:
    """Similarity in [0, 1] plus the method that produced it (for explanations)."""
    if not a or not b:
        return 0.0, "missing"
    if basic_casefold(a) == basic_casefold(b):
        return 1.0, "exact"
    na, nb = normalize_username(a), normalize_username(b)
    if not na or not nb:
        return 0.0, "empty_after_normalisation"
    if na == nb:
        return 0.97, "equal_after_normalisation"
    best, method = _fuzzy(na, nb), "fuzzy"
    if squash_repeats(na) == squash_repeats(nb):
        best, method = max((best, method), (0.93, "equal_ignoring_repeated_letters"))
    core_a, _ = strip_affixes(na)
    core_b, _ = strip_affixes(nb)
    if core_a == core_b and len(core_a) >= 4 and (core_a != na or core_b != nb):
        best, method = max((best, method), (0.86, "equal_after_affix_removal"))
    elif min(len(core_a), len(core_b)) >= 4 and (core_a != na or core_b != nb):
        cf = min(_fuzzy(core_a, core_b) * 0.92, 0.85)
        if cf > best:
            best, method = cf, "fuzzy_after_affix_removal"
    da, db = strip_trailing_digits(na), strip_trailing_digits(nb)
    if da == db and len(da) >= 5 and (da != na or db != nb):
        best, method = max((best, method), (0.84, "equal_ignoring_trailing_digits"))
    return min(best, 0.96), method


def _name_points(score: float, weight: float, distinct: float) -> tuple[float, Strength]:
    if score < 0.70:
        return 0.0, Strength.NONE
    # names equal after normalisation (only case/separators/accents differ) earn full credit
    frac = min(1.0, (score - 0.70) / 0.27)
    return weight * frac * distinct, (Strength.SUPPORTING if score >= 0.80 else Strength.WEAK)


class UsernameMatcher:
    name = "username"

    def __init__(self, config: ScoringConfig) -> None:
        self.config = config

    def compare(self, source: Profile, candidate: Profile) -> Signal:
        if not source.username or not candidate.username:
            return unavailable(self.name, Family.NAME, "username missing")
        score, method = name_similarity(source.username, candidate.username)
        na, nb = normalize_username(source.username), normalize_username(candidate.username)
        distinct = min(name_distinctiveness(na), name_distinctiveness(nb))
        points, strength = _name_points(score, self.config.username_weight, distinct)
        return Signal(
            name=self.name,
            family=Family.NAME,
            available=True,
            score=score,
            points=points,
            strength=strength,
            detail=f"{source.username!r} vs {candidate.username!r}: {score:.0%} ({method}; "
            f"distinctiveness {distinct:.2f})",
            data={"method": method, "distinctiveness": round(distinct, 3)},
        )


class DisplayNameMatcher:
    name = "display_name"

    def __init__(self, config: ScoringConfig) -> None:
        self.config = config

    def compare(self, source: Profile, candidate: Profile) -> Signal:
        s_names = [n for n in (source.display_name, source.username) if n]
        c_names = [n for n in (candidate.display_name, candidate.username) if n]
        if not source.display_name and not candidate.display_name:
            return unavailable(self.name, Family.NAME, "no display names")
        best, method, pair = 0.0, "missing", ("", "")
        for a in s_names:
            for b in c_names:
                if a is source.username and b is candidate.username:
                    continue  # that pair is the username signal
                sc, m = name_similarity(a, b)
                if sc > best:
                    best, method, pair = sc, m, (a, b)
        redundant = normalize_username(source.display_name) == normalize_username(source.username) and (
            normalize_username(candidate.display_name) == normalize_username(candidate.username)
        )
        distinct = min(
            name_distinctiveness(normalize_username(pair[0])),
            name_distinctiveness(normalize_username(pair[1])),
        )
        points, strength = _name_points(best, self.config.display_name_weight, distinct)
        detail = f"{pair[0]!r} vs {pair[1]!r}: {best:.0%} ({method})"
        if redundant:
            points *= 0.3
            strength = Strength.WEAK if strength != Strength.NONE else Strength.NONE
            detail += "; display names just restate the logins (not independent evidence)"
        return Signal(
            name=self.name,
            family=Family.NAME,
            available=True,
            score=best,
            points=points,
            strength=strength,
            detail=detail,
            data={"method": method, "redundant_with_username": redundant},
        )
