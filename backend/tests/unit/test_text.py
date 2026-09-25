from app.matching.config import ScoringConfig
from app.matching.signals import Strength
from app.matching.text import BioMatcher, ContentMatcher, CountryMatcher, bio_tokens, normalize_language
from app.platforms.base import Profile

CFG = ScoringConfig()


def P(platform="kick", **kw):
    return Profile(platform, kw.pop("username", "x"), **kw)


def test_generic_bio_is_unavailable():
    a = P(description="Gaming streamer! Follow me on twitch and discord")
    b = P("twitch", description="Streamer gaming, live every day")
    sig = BioMatcher(CFG).compare(a, b)
    assert sig.available is False


def test_bio_tokens_strip_urls_handles_and_boilerplate():
    toks = bio_tokens("Bienvenue ! Streamer VALORANT https://x.com/me @me contact: a@b.com")
    assert "valorant" in toks
    assert not any(t in toks for t in ("bienvenue", "streamer", "me", "contact", "https"))


def test_similar_distinctive_bios_score_high():
    a = P(description="Ex-pro VALORANT player from Lyon, coach of Team Zephyr, lover of cats and ramen")
    b = P("twitch", description="Former pro VALORANT player from Lyon — coach of Team Zephyr. Cats + ramen")
    sig = BioMatcher(CFG).compare(a, b)
    assert sig.score >= 0.5 and sig.strength in (Strength.SUPPORTING, Strength.STRONG)


def test_identical_long_bio_is_strong():
    text = (
        "Champion régional de Tetris 2019 et créateur du défi des cent parties sans pause, "
        "je joue aussi aux jeux rétro japonais rares collectionnés depuis Nantes"
    )
    sig = BioMatcher(CFG).compare(P(description=text), P("twitch", description=text))
    assert sig.strength == Strength.STRONG


def test_unrelated_bios_score_low():
    a = P(description="Italian chef cooking pasta recipes live from Napoli kitchen")
    b = P("twitch", description="Speedrunner of Super Mario 64, world record attempts, Berlin")
    assert BioMatcher(CFG).compare(a, b).points < 2


def test_language_normalisation():
    assert normalize_language("English") == "en"
    assert normalize_language("fr") == "fr"
    assert normalize_language("Español") == "es"
    assert normalize_language("pt-BR") == "pt"
    assert normalize_language("") is None


def test_generic_category_is_weak():
    a = P(category="Just Chatting", language="fr")
    b = P("twitch", category="Just Chatting", language="fr")
    sig = ContentMatcher(CFG).compare(a, b)
    assert sig.strength == Strength.WEAK
    assert sig.data["category_specific_match"] is False


def test_specific_category_same_language_is_supporting():
    a = P(category="Hollow Knight", language="French", tags=["speedrun", "français"])
    b = P("twitch", category="Hollow Knight", language="fr", tags=["Speedrun"])
    sig = ContentMatcher(CFG).compare(a, b)
    assert sig.strength == Strength.SUPPORTING
    assert sig.points <= CFG.content_weight


def test_different_games_no_penalty_below_zero():
    sig = ContentMatcher(CFG).compare(P(category="Fortnite"), P("twitch", category="Minecraft"))
    assert sig.points == 0


def test_country_is_support_only():
    m = CountryMatcher(CFG)
    same = m.compare("France", P("twitch", language="fr"))
    diff = m.compare("France", P("twitch", language="de"))
    assert same.points == CFG.country_weight and same.strength == Strength.WEAK
    assert diff.points == 0  # different country never eliminates or penalises
    assert m.compare(None, P("twitch", language="fr")).available is False
