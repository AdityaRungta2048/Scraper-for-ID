"""Multilingual text / username normalisation primitives."""

from __future__ import annotations

import re
import unicodedata

_SPECIAL_FOLDS = str.maketrans(
    {
        "ß": "ss",
        "æ": "ae",
        "Æ": "ae",
        "œ": "oe",
        "Œ": "oe",
        "ø": "o",
        "Ø": "o",
        "ł": "l",
        "Ł": "l",
        "đ": "d",
        "Đ": "d",
        "ð": "d",
        "þ": "th",
        "ı": "i",
    }
)
SEPARATORS_RE = re.compile(r"[\s_\-\.·•|:/\\]+")
NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
REPEAT_RE = re.compile(r"(.)\1+")

# Common creator-handle affixes. Stripping is only ever used as ONE feature.
AFFIXES = (
    "official",
    "oficial",
    "officiel",
    "offiziell",
    "ufficiale",
    "officiale",
    "gaming",
    "gamer",
    "games",
    "tv",
    "ttv",
    "live",
    "yt",
    "gg",
    "real",
    "the",
    "its",
    "itz",
    "iam",
    "twitch",
    "kick",
    "stream",
    "streams",
    "streamer",
    "channel",
    "clips",
)
_AFFIXES_SORTED = sorted(AFFIXES, key=len, reverse=True)


def fold_accents(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_SPECIAL_FOLDS)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def basic_casefold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def normalize_username(text: str | None) -> str:
    """lowercase + accent fold + remove separators / punctuation.  "Mr_Beast" -> "mrbeast"."""
    if not text:
        return ""
    return NON_ALNUM_RE.sub("", fold_accents(text).casefold())


def squash_repeats(text: str) -> str:
    """ "jimmyboyyy" -> "jimyboy" (feature only)."""
    return REPEAT_RE.sub(r"\1", text)


def strip_affixes(norm: str, min_core: int = 3) -> tuple[str, list[str]]:
    """Iteratively remove known prefixes/suffixes while at least ``min_core`` chars remain."""
    core = norm
    removed: list[str] = []
    changed = True
    while changed:
        changed = False
        for affix in _AFFIXES_SORTED:
            if core.endswith(affix) and len(core) - len(affix) >= min_core:
                core = core[: -len(affix)]
                removed.append("-" + affix)
                changed = True
                break
            if core.startswith(affix) and len(core) - len(affix) >= min_core:
                core = core[len(affix) :]
                removed.append(affix + "-")
                changed = True
                break
    return core, removed


def strip_trailing_digits(norm: str, min_core: int = 3) -> str:
    stripped = norm.rstrip("0123456789")
    return stripped if len(stripped) >= min_core else norm


def tokenize_name(text: str | None) -> list[str]:
    if not text:
        return []
    folded = fold_accents(text).casefold()
    # split camelCase before folding case would lose it — use original for boundaries
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", fold_accents(text))
    tokens = [t for t in NON_ALNUM_RE.split(spaced.casefold()) if t]
    return tokens or [t for t in NON_ALNUM_RE.split(folded) if t]


def name_distinctiveness(norm: str) -> float:
    """How identifying a normalised name is on its own (0.35..1.0).

    Short or all-alpha common-looking names are less distinctive than long names or
    names mixing letters and digits.
    """
    if not norm:
        return 0.0
    length = len(squash_repeats(norm))
    score = (length - 2) / 6.0  # 4 chars -> 0.35, 5 -> 0.5, 8+ -> 1.0
    if any(c.isdigit() for c in norm) and any(c.isalpha() for c in norm):
        score += 0.1
    return max(0.35, min(1.0, score))


URL_RE = re.compile(
    r"(?i)\b((?:https?://|www\.)[^\s<>\"'()\[\]{}]+|"
    r"(?:[a-z0-9-]+\.)+(?:com|tv|gg|ee|ai|net|org|io|me|fr|de|es|it|co|be|link|bio|page|app|live|to)"
    r"/[^\s<>\"'()\[\]{}]*)"
)


def extract_urls(text: str | None) -> list[str]:
    if not text:
        return []
    urls = []
    for m in URL_RE.finditer(text):
        u = m.group(1).rstrip(".,;:!?)»”’'\"")
        urls.append(u)
    return urls


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = URL_RE.sub(" ", text)
    text = re.sub(r"[@#]\w+", " ", text)
    text = re.sub(r"\S+@\S+\.\S+", " ", text)
    return re.sub(r"\s+", " ", text).strip()
