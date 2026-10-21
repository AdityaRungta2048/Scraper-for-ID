"""The matching rules: exact same name, same profile picture or exact same bio — nothing else."""

from app.services.resolver import apply_match_rules, rule_match


def cand(username, platform="kick", display=None, bio=None, image=None):
    signals = [{"name": "profile_image", "strength": image}] if image else []
    return {
        "platform": platform,
        "username": username,
        "display_name": display,
        "description": bio,
        "signals": signals,
    }


def test_each_rule():
    src = {"username": "foo_bar", "description": "Daily streams  of chess"}
    assert rule_match(src, cand("foo-bar")) == "exact same name"
    assert rule_match(src, cand("other", image="strong")) == "same profile picture"
    assert rule_match(src, cand("other", bio="Daily streams of chess")) == "exact same bio"
    # similar is not enough
    assert rule_match(src, cand("foo_barr", display="foo_bar", bio="Daily streams of chess!")) is None
    assert rule_match(src, cand("other", image="supporting")) is None
    assert rule_match({"username": "x"}, cand("y", bio="")) is None  # empty bios never match


def resolution(source_status, candidates, target="kick"):
    return {
        "source_platform": "twitch",
        "target_platform": target,
        "source_status": source_status,
        "source_profile": {"username": "foo_bar"} if source_status == "EXISTS" else None,
        "decision": "REVIEW",
        "target_status": "EXISTS_UNVERIFIED" if source_status == "NOT_FOUND" else None,
        "candidates": candidates,
    }


def test_source_exists():
    hit = apply_match_rules(resolution("EXISTS", [cand("closest"), cand("foo-bar")]))
    assert (hit["decision"], hit["matched_id"], hit["review_candidate"]) == ("MATCH", "foo-bar", None)
    miss = apply_match_rules(resolution("EXISTS", [cand("closest")]))
    assert (miss["decision"], miss["matched_id"], miss["review_candidate"]) == ("NO_MATCH", None, None)


def test_source_missing_takes_the_same_name_account():
    hit = apply_match_rules(resolution("NOT_FOUND", [cand("foo-bar")]))
    assert (hit["decision"], hit["target_status"], hit["matched_id"]) == ("MATCH", "VERIFIED", "foo-bar")
    none = apply_match_rules(resolution("NOT_FOUND", []))
    assert (none["decision"], none["target_status"]) == ("NO_MATCH", "NOT_FOUND")


def test_human_decisions_and_incomplete_evidence_are_left_alone():
    confirmed = {
        **resolution("EXISTS", [cand("closest")]),
        "decision": "MATCH",
        "matched_id": "closest",
        "manual": True,
    }
    assert apply_match_rules(confirmed) == confirmed
    rejected = resolution("EXISTS", [{**cand("foo-bar"), "manual_verdict": "REJECTED"}])
    assert apply_match_rules(rejected)["decision"] == "NO_MATCH"
    assert apply_match_rules({"source_status": "EXISTS"}) == {"source_status": "EXISTS"}
