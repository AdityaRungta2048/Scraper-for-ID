import pytest

from app.matching.config import ScoringConfig
from app.matching.names import DisplayNameMatcher, UsernameMatcher, name_similarity
from app.matching.normalize import (
    fold_accents,
    name_distinctiveness,
    normalize_username,
    squash_repeats,
    strip_affixes,
)
from app.matching.signals import Strength
from app.platforms.base import Profile

CFG = ScoringConfig()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MrBeast", "mrbeast"),
        ("mr_beast", "mrbeast"),
        ("mr-beast", "mrbeast"),
        ("Mr. Beast", "mrbeast"),
        ("JoséPeña", "josepena"),
        ("Müller_TV", "mullertv"),
        ("Straße", "strasse"),
        ("François", "francois"),
        ("__x__", "x"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


def test_accent_folding_keeps_letters():
    assert fold_accents("é è ö ü ñ ç") == "e e o u n c"


def test_affix_stripping_is_a_feature_not_destructive():
    assert strip_affixes("ayzohtv")[0] == "ayzoh"
    assert strip_affixes("davidsantosoficial")[0] == "davidsantos"
    assert strip_affixes("thelivegaming")[0] == "thelive" or len(strip_affixes("thelivegaming")[0]) >= 3
    # never strips below 3 characters
    assert strip_affixes("tvlive")[0] in {"tvlive", "tv", "live"} and len(strip_affixes("tvlive")[0]) >= 3


def test_squash_repeats():
    assert squash_repeats("jimmyboyyy") == "jimyboy"


def test_distinctiveness_short_names_are_less_identifying():
    assert name_distinctiveness("noni") < name_distinctiveness("bobinice7676")
    assert name_distinctiveness("bobinice7676") == 1.0


@pytest.mark.parametrize(
    ("a", "b", "low", "high"),
    [
        ("starwraith", "starwraith", 1.0, 1.0),  # exact
        ("MrBeast", "mr_beast", 0.97, 0.97),  # normalized equal
        ("quentin-cey", "quentin_cey", 0.97, 0.97),  # hyphen vs underscore
        ("ayzoh", "ayzohtv", 0.84, 0.9),  # TV suffix
        ("marco", "marcolive", 0.8, 0.9),  # live suffix
        ("davidsantos-", "davidsantos_oficial", 0.84, 0.9),  # official suffix
        ("nikkilve", "NikkLive_", 0.75, 0.9),  # similar
        ("jimmyboyyy", "jimmyboy", 0.9, 0.96),  # repeated letters
        ("bobinice7676", "bobinice76", 0.84, 0.96),  # trailing digits
        ("xqc", "pokimane", 0.0, 0.5),  # unrelated
    ],
)
def test_name_similarity_ranges(a, b, low, high):
    score, _ = name_similarity(a, b)
    assert low <= score <= high, (a, b, score)


def test_abc_vs_abcgaming_not_treated_as_same():
    score, method = name_similarity("abc", "abcgaming")
    assert method != "equal_after_affix_removal"
    assert score < 0.85


def test_username_matcher_never_strong():
    p1 = Profile("kick", "alex123")
    p2 = Profile("twitch", "alex123")
    sig = UsernameMatcher(CFG).compare(p1, p2)
    assert sig.score == 1.0
    assert sig.strength == Strength.SUPPORTING  # names are never STRONG evidence
    assert sig.points <= CFG.username_weight


def test_short_common_names_get_fewer_points():
    long = UsernameMatcher(CFG).compare(Profile("kick", "bobinice7676"), Profile("twitch", "bobinice7676"))
    short = UsernameMatcher(CFG).compare(Profile("kick", "noni"), Profile("twitch", "noni"))
    assert short.points < long.points


def test_display_name_redundant_with_login_is_discounted():
    a = Profile("kick", "fekah", display_name="Fekah")
    b = Profile("twitch", "fekah_", display_name="Fekah_")
    sig = DisplayNameMatcher(CFG).compare(a, b)
    assert sig.data["redundant_with_username"] is True
    assert sig.strength in (Strength.WEAK, Strength.NONE)


def test_same_display_name_different_logins():
    a = Profile("kick", "the-real-abc", display_name="ABC")
    b = Profile("twitch", "abcofficialtv", display_name="ABC")
    sig = DisplayNameMatcher(CFG).compare(a, b)
    assert sig.score == 1.0
    assert sig.strength == Strength.SUPPORTING
