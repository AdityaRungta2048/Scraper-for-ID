"""The matching rules: exact same name, same profile picture or exact same bio — nothing else."""

from app.services.resolver import apply_match_rules, rule_match


def cand(
    username, platform="kick", display=None, bio=None, image=None, category=None, language=None, signals=None
):
    sig = dict(signals or {})
    if image:
        sig["profile_image"] = image
    return {
        "platform": platform,
        "username": username,
        "display_name": display,
        "description": bio,
        "category": category,
        "language": language,
        "signals": [{"name": n, "strength": v} for n, v in sig.items()],
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


def test_context_contradiction_sends_a_single_rule_hit_to_review():
    res = resolution("EXISTS", [cand("foo-bar", language="pt")])
    res["source_profile"]["language"] = "fr"
    out = apply_match_rules(res)
    assert (out["decision"], out["matched_id"], out["review_candidate"]) == ("REVIEW", None, "foo-bar")
    assert "different language" in out["reason"]
    country = resolution("EXISTS", [cand("foo-bar", language="pt")])
    country["country"] = "France"
    assert apply_match_rules(country)["decision"] == "REVIEW"
    category = resolution("EXISTS", [cand("foo-bar", category="Stardew Valley")])
    category["source_profile"]["category"] = "Elden Ring"
    assert apply_match_rules(category)["decision"] == "REVIEW"
    generic = resolution("EXISTS", [cand("foo-bar", category="Just Chatting")])
    generic["source_profile"]["category"] = "Fortnite"
    assert apply_match_rules(generic)["decision"] == "MATCH"  # a generic category says nothing


def test_two_rule_hits_outweigh_context_but_not_a_link_elsewhere():
    res = resolution("EXISTS", [cand("foo-bar", language="pt", image="strong")])
    res["source_profile"]["language"] = "fr"
    assert apply_match_rules(res)["decision"] == "MATCH"
    linked = resolution("EXISTS", [cand("foo-bar", image="strong", signals={"cross_link": "conflict"})])
    assert apply_match_rules(linked)["decision"] == "REVIEW"
    pictures = resolution("EXISTS", [cand("foo-bar", signals={"profile_image": "negative"})])
    assert apply_match_rules(pictures)["decision"] == "REVIEW"


def test_ties_and_near_misses_go_to_review():
    tie = apply_match_rules(resolution("EXISTS", [cand("a1", image="strong"), cand("a2", image="strong")]))
    assert tie["decision"] == "REVIEW" and "several accounts" in tie["reason"]
    near = resolution("EXISTS", [{**cand("foobar_official"), "decision": "REVIEW", "confidence": 70.0}])
    out = apply_match_rules(near)
    assert (out["decision"], out["review_candidate"]) == ("REVIEW", "foobar_official")
    assert out["candidates"][0]["decision"] == "REVIEW"  # listed on the Review page


def test_source_missing_same_name_with_wrong_language_for_country_is_reviewed():
    res = resolution("NOT_FOUND", [cand("foo-bar", language="en")])
    res["country"] = "Spain"
    out = apply_match_rules(res)
    assert (out["decision"], out["target_status"], out["matched_id"]) == ("REVIEW", "EXISTS_UNVERIFIED", None)
