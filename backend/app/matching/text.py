"""BioMatcher, ContentMatcher and CountryMatcher (multilingual)."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol

from app.matching.config import ScoringConfig
from app.matching.normalize import clean_text, fold_accents
from app.matching.signals import Family, Signal, Strength, unavailable
from app.platforms.base import Profile

# Multilingual stopwords (en/fr/de/es/it) — compact lists of the most frequent words.
STOPWORDS = set(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "from",
        "by",
        "is",
        "are",
        "was",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "he",
        "she",
        "they",
        "them",
        "his",
        "her",
        "as",
        "not",
        "no",
        "so",
        "if",
        "than",
        "then",
        "just",
        "all",
        "any",
        "more",
        "most",
        "very",
        "can",
        "will",
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "du",
        "de",
        "d",
        "l",
        "et",
        "ou",
        "mais",
        "en",
        "dans",
        "sur",
        "pour",
        "par",
        "avec",
        "sans",
        "ce",
        "cet",
        "cette",
        "ces",
        "je",
        "tu",
        "il",
        "elle",
        "nous",
        "vous",
        "ils",
        "elles",
        "mon",
        "ma",
        "mes",
        "ton",
        "ta",
        "tes",
        "son",
        "sa",
        "ses",
        "notre",
        "votre",
        "leur",
        "leurs",
        "est",
        "sont",
        "suis",
        "es",
        "etre",
        "ai",
        "as",
        "a",
        "avons",
        "qui",
        "que",
        "quoi",
        "dont",
        "ou",
        "ne",
        "pas",
        "plus",
        "tres",
        "tout",
        "tous",
        "toute",
        "toutes",
        "y",
        "au",
        "aux",
        "der",
        "die",
        "das",
        "ein",
        "eine",
        "einer",
        "eines",
        "und",
        "oder",
        "aber",
        "von",
        "zu",
        "im",
        "in",
        "am",
        "an",
        "auf",
        "fur",
        "mit",
        "aus",
        "bei",
        "ist",
        "sind",
        "bin",
        "bist",
        "sein",
        "ich",
        "du",
        "er",
        "sie",
        "es",
        "wir",
        "ihr",
        "mein",
        "meine",
        "dein",
        "deine",
        "sein",
        "seine",
        "unser",
        "euer",
        "nicht",
        "kein",
        "keine",
        "auch",
        "noch",
        "nur",
        "sehr",
        "den",
        "dem",
        "des",
        "zum",
        "zur",
        "als",
        "wie",
        "wenn",
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "y",
        "o",
        "pero",
        "de",
        "del",
        "al",
        "en",
        "con",
        "sin",
        "por",
        "para",
        "es",
        "son",
        "soy",
        "eres",
        "ser",
        "yo",
        "tu",
        "el",
        "ella",
        "nosotros",
        "vosotros",
        "ellos",
        "mi",
        "mis",
        "tu",
        "tus",
        "su",
        "sus",
        "nuestro",
        "que",
        "no",
        "mas",
        "muy",
        "todo",
        "todos",
        "toda",
        "todas",
        "lo",
        "le",
        "les",
        "se",
        "il",
        "lo",
        "la",
        "gli",
        "le",
        "un",
        "uno",
        "una",
        "e",
        "o",
        "ma",
        "di",
        "da",
        "in",
        "con",
        "su",
        "per",
        "tra",
        "fra",
        "sono",
        "sei",
        "essere",
        "io",
        "tu",
        "lui",
        "lei",
        "noi",
        "voi",
        "loro",
        "mio",
        "mia",
        "miei",
        "tuo",
        "tua",
        "suo",
        "sua",
        "nostro",
        "che",
        "non",
        "piu",
        "molto",
        "tutto",
        "tutti",
        "ci",
        "si",
    ]
)

# Promotional / boilerplate vocabulary common to streamer bios in all target languages.
GENERIC_BIO_WORDS = set(
    [
        "stream",
        "streams",
        "streamer",
        "streameur",
        "streamerin",
        "streaming",
        "live",
        "lives",
        "direct",
        "directo",
        "diretta",
        "en_vivo",
        "twitch",
        "kick",
        "youtube",
        "instagram",
        "tiktok",
        "twitter",
        "discord",
        "insta",
        "ig",
        "yt",
        "tt",
        "x",
        "snapchat",
        "facebook",
        "gaming",
        "gamer",
        "gamers",
        "game",
        "games",
        "jeu",
        "jeux",
        "jeuxvideo",
        "spiele",
        "spiel",
        "juegos",
        "juego",
        "giochi",
        "gioco",
        "videojuegos",
        "channel",
        "chaine",
        "chaîne",
        "kanal",
        "canal",
        "canale",
        "welcome",
        "bienvenue",
        "willkommen",
        "bienvenido",
        "bienvenida",
        "benvenuto",
        "benvenuti",
        "follow",
        "followme",
        "suivez",
        "folgt",
        "sigueme",
        "seguimi",
        "sub",
        "subs",
        "subscribe",
        "abonne",
        "abonnez",
        "abonniert",
        "suscribete",
        "iscriviti",
        "business",
        "contact",
        "contacto",
        "contatto",
        "kontakt",
        "email",
        "mail",
        "pro",
        "partner",
        "partenaire",
        "every",
        "day",
        "daily",
        "quotidien",
        "quotidiennement",
        "taglich",
        "diario",
        "giornaliero",
        "tous",
        "jours",
        "jeden",
        "tag",
        "todos",
        "dias",
        "tutti",
        "giorni",
        "schedule",
        "planning",
        "horaires",
        "programme",
        "zeitplan",
        "horario",
        "orari",
        "fun",
        "funny",
        "chill",
        "vibes",
        "community",
        "communaute",
        "communauté",
        "gemeinschaft",
        "comunidad",
        "comunita",
        "content",
        "creator",
        "createur",
        "creador",
        "creatore",
        "hello",
        "hi",
        "hey",
        "salut",
        "hallo",
        "hola",
        "ciao",
        "merci",
        "thanks",
        "danke",
        "gracias",
        "grazie",
        "love",
    ]
)

TOKEN_RE = re.compile(r"[a-z0-9]+")


def bio_tokens(text: str | None) -> list[str]:
    cleaned = fold_accents(clean_text(text)).casefold()
    return [
        t
        for t in TOKEN_RE.findall(cleaned)
        if len(t) > 1 and t not in STOPWORDS and t not in GENERIC_BIO_WORDS
    ]


def _char_ngrams(text: str, n: int = 3) -> Counter[str]:
    t = f" {text} "
    return Counter(t[i : i + n] for i in range(len(t) - n + 1))


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _longest_common_run(a: list[str], b: list[str]) -> int:
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


class TextEmbedder(Protocol):
    def similarity(self, a: str, b: str) -> float: ...


class SentenceTransformerEmbedder:
    """Optional multilingual semantic similarity (requires the `ml` extra)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

        self.model = SentenceTransformer(model_name)
        self._cache: dict[str, object] = {}

    def _embed(self, text: str) -> object:
        if text not in self._cache:
            self._cache[text] = self.model.encode(text, normalize_embeddings=True)
        return self._cache[text]

    def similarity(self, a: str, b: str) -> float:
        import numpy as np

        return float(np.dot(self._embed(a), self._embed(b)))  # type: ignore[call-overload]


class BioMatcher:
    name = "bio"

    def __init__(self, config: ScoringConfig, embedder: TextEmbedder | None = None) -> None:
        self.config = config
        self.embedder = embedder

    def compare(self, source: Profile, candidate: Profile) -> Signal:
        ta, tb = bio_tokens(source.all_text), bio_tokens(candidate.all_text)
        if len(ta) < 3 or len(tb) < 3:
            return unavailable(self.name, Family.TEXT, "bio missing or too generic on at least one profile")
        sa, sb = set(ta), set(tb)
        jaccard = len(sa & sb) / len(sa | sb)
        char_cos = _cosine(_char_ngrams(" ".join(sorted(sa))), _char_ngrams(" ".join(sorted(sb))))
        lexical = max(jaccard, 0.9 * char_cos if jaccard > 0.1 else 0.6 * char_cos)
        semantic = None
        if self.embedder is not None:
            try:
                emb = self.embedder.similarity(clean_text(source.all_text), clean_text(candidate.all_text))
                semantic = max(0.0, min(1.0, (emb - 0.55) / 0.4))
            except Exception:
                semantic = None
        score = max(lexical, semantic or 0.0)
        run = _longest_common_run(ta, tb)
        points = self.config.bio_weight * max(0.0, min(1.0, (score - 0.3) / 0.5))
        strength = Strength.SUPPORTING if score >= 0.5 else (Strength.WEAK if points > 0 else Strength.NONE)
        detail = f"bio similarity {score:.0%} (lexical {lexical:.0%}"
        detail += f", semantic {semantic:.0%})" if semantic is not None else ")"
        if run >= 10:
            strength = Strength.STRONG
            points = max(points, self.config.bio_weight)
            detail += f"; identical distinctive passage of {run} words"
        elif run >= 5:
            points = max(points, self.config.bio_weight * 0.8)
            strength = Strength.SUPPORTING
            detail += f"; shared distinctive phrase ({run} words)"
        return Signal(
            self.name,
            Family.TEXT,
            True,
            score,
            points,
            strength,
            detail,
            {
                "jaccard": round(jaccard, 3),
                "char_cosine": round(char_cos, 3),
                "common_run": run,
                "semantic": None if semantic is None else round(semantic, 3),
            },
        )


# ------------------------------------------------------------------------------------
LANGUAGE_NAMES = {
    "english": "en",
    "anglais": "en",
    "englisch": "en",
    "ingles": "en",
    "inglese": "en",
    "french": "fr",
    "francais": "fr",
    "franzosisch": "fr",
    "frances": "fr",
    "francese": "fr",
    "german": "de",
    "deutsch": "de",
    "allemand": "de",
    "aleman": "de",
    "tedesco": "de",
    "spanish": "es",
    "espanol": "es",
    "espagnol": "es",
    "spanisch": "es",
    "spagnolo": "es",
    "castellano": "es",
    "italian": "it",
    "italiano": "it",
    "italien": "it",
    "italienisch": "it",
    "portuguese": "pt",
    "portugues": "pt",
    "polish": "pl",
    "polski": "pl",
    "turkish": "tr",
    "turkce": "tr",
    "dutch": "nl",
    "nederlands": "nl",
    "russian": "ru",
    "arabic": "ar",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "swedish": "sv",
    "norwegian": "no",
    "danish": "da",
    "finnish": "fi",
    "czech": "cs",
    "greek": "el",
    "hungarian": "hu",
    "romanian": "ro",
    "ukrainian": "uk",
}


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    v = fold_accents(value).casefold().strip()
    if v in {"other", "asl", "unknown"}:
        return None
    if len(v) == 2 and v.isalpha():
        return v
    if "-" in v[:3] or "_" in v[:3]:
        return v[:2]
    return LANGUAGE_NAMES.get(v)


GENERIC_CATEGORIES = {
    "just chatting",
    "irl",
    "talk shows & podcasts",
    "music",
    "art",
    "special events",
    "pools, hot tubs, and beaches",
    "slots",
    "slots & casino",
    "virtual casino",
    "games + demos",
    "gaming",
    "asmr",
    "fortnite",
    "grand theft auto v",
    "gta v",
    "league of legends",
    "valorant",
    "counter-strike",
    "counter-strike 2",
    "minecraft",
    "call of duty: warzone",
    "call of duty",
    "ea sports fc 25",
    "ea sports fc 26",
    "fifa",
    "apex legends",
    "rocket league",
    "chess",
    "software and game development",
    "sports",
}


def _norm_tag(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold_accents(t).casefold())


class ContentMatcher:
    name = "content"

    def __init__(self, config: ScoringConfig) -> None:
        self.config = config

    def compare(self, source: Profile, candidate: Profile) -> Signal:
        parts: list[tuple[float, float]] = []  # (weight, score)
        details: list[str] = []
        cat_equal_specific = False
        if source.category and candidate.category:
            a, b = source.category.casefold().strip(), candidate.category.casefold().strip()
            if a == b:
                generic = a in GENERIC_CATEGORIES
                parts.append((0.45, 0.3 if generic else 1.0))
                cat_equal_specific = not generic
                details.append(f"same category '{source.category}'" + (" (generic)" if generic else ""))
            else:
                parts.append((0.45, 0.0))
                details.append(f"different category ({source.category} / {candidate.category})")
        tags_overlap = 0.0
        ta = {_norm_tag(t) for t in source.tags if _norm_tag(t)}
        tb = {_norm_tag(t) for t in candidate.tags if _norm_tag(t)}
        la, lb = normalize_language(source.language), normalize_language(candidate.language)
        ta.discard(la or "")
        tb.discard(lb or "")
        if ta and tb:
            tags_overlap = len(ta & tb) / len(ta | tb)
            parts.append((0.25, tags_overlap))
            details.append(f"tag overlap {tags_overlap:.0%}")
        lang_equal = False
        if la and lb:
            lang_equal = la == lb
            parts.append((0.2, 1.0 if lang_equal else 0.0))
            details.append(f"language {'same' if lang_equal else 'different'} ({la}/{lb})")
        if source.stream_title and candidate.stream_title:
            wa, wb = set(bio_tokens(source.stream_title)), set(bio_tokens(candidate.stream_title))
            if wa and wb:
                tj = len(wa & wb) / len(wa | wb)
                parts.append((0.1, tj))
                details.append(f"title overlap {tj:.0%}")
        if not parts:
            return unavailable(self.name, Family.CONTENT, "no comparable category/tags/language")
        total_w = sum(w for w, _ in parts)
        score = sum(w * s for w, s in parts) / total_w
        points = self.config.content_weight * score
        supporting = cat_equal_specific and (lang_equal or tags_overlap >= 0.3)
        strength = Strength.SUPPORTING if supporting else (Strength.WEAK if points > 0 else Strength.NONE)
        return Signal(
            self.name,
            Family.CONTENT,
            True,
            score,
            points,
            strength,
            "; ".join(details),
            {
                "category_specific_match": cat_equal_specific,
                "language_match": lang_equal,
                "tag_overlap": round(tags_overlap, 3),
            },
        )


COUNTRY_LANGUAGES: dict[str, set[str]] = {
    "germany": {"de"},
    "deutschland": {"de"},
    "austria": {"de"},
    "osterreich": {"de"},
    "switzerland": {"de", "fr", "it"},
    "schweiz": {"de", "fr", "it"},
    "suisse": {"de", "fr", "it"},
    "france": {"fr"},
    "belgium": {"fr", "nl"},
    "belgique": {"fr", "nl"},
    "luxembourg": {"fr", "de"},
    "canada": {"en", "fr"},
    "quebec": {"fr"},
    "spain": {"es"},
    "espana": {"es"},
    "mexico": {"es"},
    "argentina": {"es"},
    "colombia": {"es"},
    "chile": {"es"},
    "peru": {"es"},
    "venezuela": {"es"},
    "uruguay": {"es"},
    "italy": {"it"},
    "italia": {"it"},
    "portugal": {"pt"},
    "brazil": {"pt"},
    "brasil": {"pt"},
    "united kingdom": {"en"},
    "uk": {"en"},
    "england": {"en"},
    "ireland": {"en"},
    "usa": {"en"},
    "united states": {"en"},
    "us": {"en"},
    "australia": {"en"},
    "netherlands": {"nl"},
    "poland": {"pl"},
    "turkey": {"tr"},
    "turkiye": {"tr"},
    "sweden": {"sv"},
    "norway": {"no"},
    "denmark": {"da"},
    "finland": {"fi"},
    "czechia": {"cs"},
    "czech republic": {"cs"},
    "greece": {"el"},
    "hungary": {"hu"},
    "romania": {"ro"},
    "russia": {"ru"},
    "ukraine": {"uk"},
    "japan": {"ja"},
    "korea": {"ko"},
    "south korea": {"ko"},
}


class CountryMatcher:
    """Country is supporting evidence only: small bonus when consistent, never a penalty."""

    name = "country"

    def __init__(self, config: ScoringConfig) -> None:
        self.config = config

    def compare(self, country: str | None, candidate: Profile) -> Signal:
        if not country:
            return unavailable(self.name, Family.COUNTRY, "no country in the sheet")
        langs = COUNTRY_LANGUAGES.get(fold_accents(country).casefold().strip())
        cand_lang = normalize_language(candidate.language)
        if not langs or not cand_lang:
            return unavailable(
                self.name, Family.COUNTRY, f"cannot relate country '{country}' to candidate language"
            )
        if cand_lang in langs:
            return Signal(
                self.name,
                Family.COUNTRY,
                True,
                1.0,
                self.config.country_weight,
                Strength.WEAK,
                f"candidate broadcasts in {cand_lang}, consistent with {country}",
            )
        return Signal(
            self.name,
            Family.COUNTRY,
            True,
            0.0,
            0.0,
            Strength.NONE,
            f"candidate broadcasts in {cand_lang}; sheet says {country} (not penalised)",
        )
