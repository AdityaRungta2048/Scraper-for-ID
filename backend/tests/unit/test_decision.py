"""Confidence scoring + gating rules (docs/DESIGN.md §F)."""

from app.matching.config import ScoringConfig
from app.matching.decision import CandidateEvaluation, ConfidenceScorer, Decision, DecisionEngine, aggregate
from app.matching.signals import Family, Signal, Strength
from app.platforms.base import Profile

CFG = ScoringConfig()
ENGINE = DecisionEngine(CFG)


def sig(name, family, points, strength, score=1.0, **data):
    return Signal(name, family, True, score, points, strength, f"{name} detail", data)


def username(points=15.0, score=1.0):
    return sig("username", Family.NAME, points, Strength.SUPPORTING, score)


def display(points=10.0):
    return sig("display_name", Family.NAME, points, Strength.SUPPORTING)


def image(points=20.0, strength=Strength.STRONG, score=0.97):
    return sig("profile_image", Family.IMAGE, points, strength, score, generic=False)


def social(points=25.0):
    return sig(
        "shared_socials",
        Family.SOCIAL,
        points,
        Strength.STRONG,
        shared_unique=["instagram:x"],
        instagram_match=True,
    )


def link(points=30.0, forward=True, backward=False):
    return sig("cross_link", Family.LINK, points, Strength.STRONG, forward=forward, backward=backward)


def country():
    return sig("country", Family.COUNTRY, 2.0, Strength.WEAK)


def content(points=4.0, strength=Strength.SUPPORTING):
    return sig("content", Family.CONTENT, points, strength, 0.9)


def evaluate(*signals, username_="cand"):
    return ENGINE.evaluate(CandidateEvaluation(Profile("twitch", username_), list(signals)))


def test_confidence_curve_monotonic_and_bounded():
    s = ConfidenceScorer(CFG)
    values = [s.confidence(p) for p in (0, 10, 20, 40, 80, 200)]
    assert values == sorted(values) and values[0] == 0 and values[-1] <= 100


def test_name_family_is_capped():
    s = ConfidenceScorer(CFG)
    assert s.points([username(15), display(10)]) == CFG.name_family_cap


def test_candidate_a_username_and_country_only_never_match():
    # spec §19 Candidate A: username 99%, country same, everything else different
    ev = evaluate(username(15, 0.99), country(), image(-6, Strength.NEGATIVE, 0.4))
    assert ev.decision != Decision.MATCH
    assert ev.gates["name_only_cap_applied"]


def test_candidate_b_weak_username_but_strong_independent_evidence_matches():
    # spec §19 Candidate B: username 75%, same picture, same Instagram & YouTube, same content
    ev = evaluate(username(3, 0.75), image(), social(33), content())
    assert ev.decision == Decision.MATCH
    assert ev.confidence >= CFG.match_threshold


def test_identical_username_and_display_name_alone_is_review_at_most():
    ev = evaluate(username(15), display(10), country(), content(1, Strength.WEAK))
    assert ev.decision in (Decision.REVIEW, Decision.NO_MATCH)
    assert ev.confidence < CFG.match_threshold


def test_strong_image_plus_exact_username_matches():
    ev = evaluate(username(15), image(), country())
    assert ev.decision == Decision.MATCH, ev.reason


def test_single_strong_signal_without_corroboration_is_review():
    ev = evaluate(image())
    assert ev.decision == Decision.REVIEW
    assert not ev.gates["corroborated"]


def test_explicit_link_plus_supporting_name_matches():
    ev = evaluate(link(), username(8, 0.85))
    assert ev.decision == Decision.MATCH


def test_mutual_links_are_enough_even_with_unrelated_names():
    ev = evaluate(link(45, forward=True, backward=True))
    assert ev.decision == Decision.MATCH


def test_conflicting_link_blocks_match():
    conflict = sig("cross_link", Family.LINK, -40, Strength.CONFLICT, 0.0)
    ev = evaluate(conflict, image(), username(15))
    assert ev.decision == Decision.REVIEW  # strong image but explicit contradiction
    ev2 = evaluate(conflict, username(15), display(10))
    assert ev2.decision == Decision.NO_MATCH


def test_negative_image_evidence_prevents_name_based_match():
    ev = evaluate(username(15), display(10), content(), image(-6, Strength.NEGATIVE, 0.3))
    assert ev.decision != Decision.MATCH


def test_generic_signals_only_no_match():
    ev = evaluate(content(1.5, Strength.WEAK), country())
    assert ev.decision == Decision.NO_MATCH


def test_social_plus_image_matches_with_different_usernames():
    ev = evaluate(username(0, 0.2), social(), image())
    assert ev.decision == Decision.MATCH


def test_ambiguous_multiple_matches_become_review():
    a = evaluate(username(15), display(3), image(), username_="twinz")
    b = evaluate(username(12, 0.9), display(3), image(), country(), username_="twinz_tv")
    agg = aggregate([a, b])
    assert agg.decision == Decision.REVIEW
    assert "Ambiguous" in agg.reason


def test_single_match_among_others_wins():
    a = evaluate(username(15), display(3), image())
    b = evaluate(username(8, 0.85), country(), username_="other")
    assert aggregate([a, b]).decision == Decision.MATCH


def test_no_candidates():
    agg = aggregate([])
    assert agg.decision == Decision.NO_MATCH and agg.best is None


def test_explanations_present():
    ev = evaluate(username(15), image(), country())
    assert ev.reason.startswith("Strong cross-platform identity evidence")
    ev2 = evaluate(username(15), display(10))
    assert "insufficient" in ev2.reason.lower() or "not the same" in ev2.reason.lower()
    summary = ev.evidence_summary()
    assert summary["profile_image_similarity"] == 0.97 and summary["country_match"] is True
