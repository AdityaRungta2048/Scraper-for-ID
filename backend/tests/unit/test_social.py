import pytest

from app.matching.config import ScoringConfig
from app.matching.signals import Strength
from app.matching.social import SocialLinkMatcher, extract_identities, normalize_social_url
from app.platforms.base import Profile

CFG = ScoringConfig()


@pytest.mark.parametrize(
    "url",
    [
        "https://instagram.com/example",
        "https://www.instagram.com/example/",
        "instagram.com/example",
        "http://instagram.com/Example?hl=en",
        "https://m.instagram.com/example",
    ],
)
def test_instagram_variants_normalize_to_same_identity(url):
    ident = normalize_social_url(url)
    assert ident is not None
    assert (ident.kind, ident.identity, ident.unique) == ("instagram", "example", True)


@pytest.mark.parametrize(
    ("url", "kind", "identity"),
    [
        ("https://youtube.com/@CreatorABC", "youtube", "creatorabc"),
        ("https://www.youtube.com/c/CreatorABC", "youtube", "creatorabc"),
        ("https://youtube.com/channel/UCabc123", "youtube", "channel:ucabc123"),
        ("https://twitter.com/Creator", "x", "creator"),
        ("https://x.com/creator/", "x", "creator"),
        ("https://www.tiktok.com/@creator", "tiktok", "creator"),
        ("https://twitch.tv/NikkLive_", "twitch", "nikklive_"),
        ("kick.com/the_real_abc", "kick", "the-real-abc"),
        ("https://linktr.ee/creator", "linktree", "creator"),
        ("https://beacons.ai/creator", "beacons", "creator"),
        ("https://creator.carrd.co", "linkhub", "carrd.co/creator"),
    ],
)
def test_platform_url_normalization(url, kind, identity):
    ident = normalize_social_url(url)
    assert ident is not None and (ident.kind, ident.identity) == (kind, identity)


@pytest.mark.parametrize(
    "url",
    [
        "https://bit.ly/abc",  # opaque shortener
        "https://youtu.be/dQw4w9WgXcQ",  # a video, not an account
        "https://www.youtube.com/watch?v=xyz",
        "https://instagram.com/p/Cxyz",  # a post
        "https://twitter.com/intent/tweet",
    ],
)
def test_non_identity_urls_ignored(url):
    assert normalize_social_url(url) is None


def test_discord_and_sponsors_are_not_unique():
    assert normalize_social_url("https://discord.gg/abcdef").unique is False
    assert normalize_social_url("https://discord.com/invite/abcdef").unique is False
    assert normalize_social_url("https://www.instant-gaming.com/?igr=creator").unique is False
    assert normalize_social_url("https://instagram.com/kick").unique is False  # official brand account


def test_mentions_are_extracted():
    p = Profile(
        "twitch", "someone", description="Insta: @Creator.Name | Twitter @crea_tor | kick: the_real_abc"
    )
    ids = {(i.kind, i.identity) for i in extract_identities(p)}
    assert ("instagram", "creator.name") in ids
    assert ("x", "crea_tor") in ids
    assert ("kick", "the-real-abc") in ids


def test_mention_requires_separator():
    p = Profile("kick", "someone", description="French twitch streamer and kick streamer")
    assert not [i for i in extract_identities(p) if i.kind in ("twitch", "kick")]


def test_self_link_is_ignored():
    p = Profile("kick", "creator", description="kick.com/creator")
    assert extract_identities(p) == []


def _cmp(src_desc, cand_desc, src="creator", cand="creator_tv"):
    s = Profile("kick", src, description=src_desc)
    c = Profile("twitch", cand, description=cand_desc)
    return {sig.name: sig for sig in SocialLinkMatcher(CFG).compare(s, c)}


def test_explicit_forward_link_is_strong():
    sigs = _cmp("Also on twitch.tv/creator_tv", "")
    assert sigs["cross_link"].strength == Strength.STRONG
    assert sigs["cross_link"].points == CFG.cross_link_weight


def test_mutual_link_bonus():
    sigs = _cmp("twitch.tv/creator_tv", "kick.com/creator")
    assert sigs["cross_link"].data["forward"] and sigs["cross_link"].data["backward"]
    assert sigs["cross_link"].points > CFG.cross_link_weight


def test_link_to_different_account_is_conflict():
    sigs = _cmp("my twitch: twitch.tv/someoneelse", "")
    assert sigs["cross_link"].strength == Strength.CONFLICT
    assert sigs["cross_link"].points < 0


def test_same_unique_instagram_is_strong():
    sigs = _cmp("https://instagram.com/creator123", "IG: @creator123")
    s = sigs["shared_socials"]
    assert s.strength == Strength.STRONG and s.data["instagram_match"]


def test_multiple_shared_accounts_add_points():
    one = _cmp("instagram.com/c123", "instagram.com/c123")["shared_socials"].points
    two = _cmp("instagram.com/c123 youtube.com/@c123", "instagram.com/c123 youtube.com/@c123")[
        "shared_socials"
    ].points
    assert two > one


def test_generic_discord_only_is_weak():
    s = _cmp("discord.gg/team", "discord.gg/team")["shared_socials"]
    assert s.strength == Strength.WEAK
    assert s.points <= 4


def test_personal_website_vs_unrelated_domain():
    personal = _cmp("https://www.creator.fr", "creator.fr/shop")["shared_socials"]
    assert personal.strength == Strength.STRONG
    unrelated = _cmp("https://www.coolteam.gg", "https://coolteam.gg")["shared_socials"]
    assert unrelated.strength == Strength.WEAK  # shared team site ≠ same person


def test_different_accounts_same_network_is_negative():
    s = _cmp("instagram.com/alex_k", "instagram.com/alex_t")["shared_socials"]
    assert s.strength == Strength.NEGATIVE


def test_frequency_marks_widely_shared_identity_generic():
    matcher = SocialLinkMatcher(CFG, frequency=lambda kind, ident, excl: 5)
    s = Profile("kick", "a", description="instagram.com/bigorg")
    c = Profile("twitch", "b", description="instagram.com/bigorg")
    sig = {x.name: x for x in matcher.compare(s, c)}["shared_socials"]
    assert sig.strength == Strength.WEAK
