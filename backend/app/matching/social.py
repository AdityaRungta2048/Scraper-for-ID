"""Social-link extraction, normalisation and the SocialLinkMatcher.

* ``instagram.com/Example``, ``https://www.instagram.com/example/`` and
  ``IG: @example`` all normalise to ``("instagram", "example")``.
* Explicit cross-platform links (a Kick profile linking ``twitch.tv/foo``) are the
  strongest evidence; a link to a *different* account is a hard conflict.
* Discord invites, link shorteners, sponsor/affiliate domains and platform-official
  accounts are never treated as unique identity evidence.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlparse

from app.matching.config import ScoringConfig
from app.matching.normalize import extract_urls, fold_accents, normalize_username
from app.matching.signals import Family, Signal, Strength
from app.platforms.base import Profile


@dataclass(frozen=True)
class SocialIdentity:
    kind: str  # instagram | youtube | x | tiktok | twitch | kick | website | linktree | discord | ...
    identity: str
    url: str
    unique: bool = True  # False => generic / shared (discord, sponsor, shortener)
    via: str = "url"  # url | mention | profile_field

    def key(self) -> tuple[str, str]:
        return (self.kind, self.identity)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_HOST_KIND = {
    "instagram.com": "instagram",
    "instagr.am": "instagram",
    "youtube.com": "youtube",
    "youtu.be": "youtube_video",
    "music.youtube.com": "youtube",
    "twitter.com": "x",
    "x.com": "x",
    "mobile.twitter.com": "x",
    "tiktok.com": "tiktok",
    "vm.tiktok.com": "tiktok_short",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "fb.me": "facebook",
    "threads.net": "threads",
    "threads.com": "threads",
    "snapchat.com": "snapchat",
    "reddit.com": "reddit",
    "github.com": "github",
    "twitch.tv": "twitch",
    "kick.com": "kick",
    "discord.gg": "discord",
    "discord.com": "discord",
    "discordapp.com": "discord",
    "linktr.ee": "linktree",
    "beacons.ai": "beacons",
    "allmylinks.com": "linkhub",
    "bio.link": "linkhub",
    "lnk.bio": "linkhub",
    "solo.to": "linkhub",
    "taplink.cc": "linkhub",
    "campsite.bio": "linkhub",
    "linkin.bio": "linkhub",
    "hoo.be": "linkhub",
    "withkoji.com": "linkhub",
    "streamlabs.com": "streamlabs",
    "streamelements.com": "streamelements",
    "tipeee.com": "tipeee",
    "ko-fi.com": "kofi",
    "patreon.com": "patreon",
    "paypal.me": "paypal",
    "throne.com": "throne",
    "buymeacoffee.com": "buymeacoffee",
    "spotify.com": "spotify",
    "open.spotify.com": "spotify",
    "soundcloud.com": "soundcloud",
    "vk.com": "vk",
    "t.me": "telegram",
    "telegram.me": "telegram",
    "steamcommunity.com": "steam",
}
_SHORTENERS = {
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "goo.gl",
    "ow.ly",
    "rebrand.ly",
    "cutt.ly",
    "shorturl.at",
    "is.gd",
    "buff.ly",
    "tiny.cc",
    "rb.gy",
    "s.id",
    "linktw.in",
    "lnkd.in",
}
# Brand / sponsor / store domains: many unrelated creators link these.
_GENERIC_DOMAINS = {
    "amazon.com",
    "amazon.fr",
    "amazon.de",
    "amazon.es",
    "amazon.it",
    "amazon.co.uk",
    "amzn.to",
    "amzn.eu",
    "instant-gaming.com",
    "g2a.com",
    "eneba.com",
    "kinguin.net",
    "nordvpn.com",
    "surfshark.com",
    "gfuel.com",
    "razer.com",
    "logitechg.com",
    "hyperx.com",
    "corsair.com",
    "secretlab.co",
    "noblechairs.com",
    "raidshadowlegends.com",
    "plarium.com",
    "hellofresh.com",
    "stake.com",
    "roobet.com",
    "rainbet.com",
    "gamdom.com",
    "shuffle.com",
    "clash.gg",
    "hellcase.com",
    "skinclub.gg",
    "csgoroll.com",
    "google.com",
    "apple.com",
    "play.google.com",
    "apps.apple.com",
    "twitchtracker.com",
    "streamscharts.com",
    "sullygnome.com",
    "wikipedia.org",
    "epicgames.com",
    "store.steampowered.com",
    "riotgames.com",
    "blizzard.com",
    "ubisoft.com",
    "ea.com",
    "xbox.com",
    "playstation.com",
    "nintendo.com",
    "gofundme.com",
    "shop.app",
    "myshopify.com",
    "teespring.com",
    "spreadshirt.fr",
    "spreadshirt.de",
    "fourthwall.com",
}
# Official platform/brand accounts: shared by many creators, identify nobody.
_GENERIC_IDENTITIES = {
    "kick",
    "kickstreaming",
    "kickcom",
    "twitch",
    "twitchfr",
    "twitchde",
    "twitches",
    "twitchit",
    "youtube",
    "instagram",
    "tiktok",
    "discord",
    "x",
    "twitter",
    "streamlabs",
    "streamelements",
}
_RESERVED_PATHS = {
    "instagram": {"p", "reel", "reels", "tv", "explore", "accounts", "direct", "about", "legal", "stories"},
    "x": {"intent", "share", "home", "i", "hashtag", "search", "explore", "settings", "login", "signup"},
    "youtube": {"watch", "shorts", "playlist", "results", "feed", "embed", "live", "redirect", "hashtag"},
    "tiktok": {"tag", "music", "discover", "video", "embed"},
    "facebook": {"groups", "events", "watch", "sharer", "sharer.php", "share", "login", "photo", "photos"},
    "twitch": {
        "directory",
        "videos",
        "p",
        "settings",
        "search",
        "downloads",
        "jobs",
        "turbo",
        "prime",
        "subscriptions",
        "inventory",
        "wallet",
        "drops",
        "team",
        "popout",
        "embed",
    },
    "kick": {
        "categories",
        "category",
        "search",
        "browse",
        "video",
        "videos",
        "clips",
        "clip",
        "api",
        "community-guidelines",
        "terms-of-service",
        "privacy-policy",
        "following",
        "settings",
    },
}
_HANDLE_OK = re.compile(r"^[a-z0-9._-]{1,64}$")

_MENTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("instagram", re.compile(r"(?i)\b(?:instagram|insta|ig)\s*(?:[:\-–→>|]+\s*@?|@)([a-z0-9._]{2,30})")),
    ("x", re.compile(r"(?i)\b(?:twitter|x)\s*(?:[:\-–→>|]+\s*@?|@)([a-z0-9_]{2,15})\b")),
    ("tiktok", re.compile(r"(?i)\b(?:tiktok|tik\s*tok|tt)\s*(?:[:\-–→>|]+\s*@?|@)([a-z0-9._]{2,24})")),
    ("youtube", re.compile(r"(?i)\b(?:youtube|yt)\s*(?:[:\-–→>|]+\s*@?|@)([a-z0-9._-]{3,30})")),
    ("twitch", re.compile(r"(?i)\btwitch\s*(?:[:\-–→>|]+\s*@?|@)([a-z0-9_]{3,25})\b")),
    ("kick", re.compile(r"(?i)\bkick\s*(?:[:\-–→>|]+\s*@?|@)([a-z0-9_-]{3,25})\b")),
]


def canonical_platform_handle(kind: str, handle: str) -> str:
    h = handle.lower().lstrip("@")
    if kind == "kick":
        return h.replace("_", "-")
    return h


def normalize_social_url(url: str, via: str = "url") -> SocialIdentity | None:
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "m.", "mobile.", "web."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix) :]
    if not host or "." not in host:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    clean_url = f"https://{host}/{'/'.join(parts)}".rstrip("/")

    if host in _SHORTENERS:
        return None  # opaque: cannot identify anyone without following the redirect
    # Subdomain link hubs (name.carrd.co, name.bio.link etc.)
    if host.endswith(".carrd.co") or host.endswith(".bio.link") or host.endswith(".linktr.ee"):
        sub = host.split(".")[0]
        return SocialIdentity("linkhub", f"{host.split('.', 1)[1]}/{sub}", clean_url, True, via)
    if host.endswith(".tiktok.com") and host != "vm.tiktok.com":
        host = "tiktok.com"
    kind = _HOST_KIND.get(host)
    if kind is None:
        for base, k in _HOST_KIND.items():
            if host.endswith("." + base):
                kind = k
                break

    if kind is None:
        domain = host
        if domain in _GENERIC_DOMAINS or any(domain.endswith("." + g) for g in _GENERIC_DOMAINS):
            return SocialIdentity("website", domain, clean_url, False, via)
        query = parse_qs(parsed.query)
        affiliate = any(
            k.lower() in {"ref", "aff", "affiliate", "code", "promo", "utm_source"} for k in query
        )
        return SocialIdentity("website", domain, clean_url, not affiliate, via)

    if kind in {"youtube_video", "tiktok_short"}:
        return None  # a video link identifies content, not an account
    if kind == "discord":
        code = parts[-1].lower() if parts else ""
        if host != "discord.gg" and (len(parts) < 2 or parts[0] not in {"invite"}):
            return None
        return SocialIdentity("discord", code, clean_url, False, via) if code else None

    if not parts:
        return None
    first = parts[0]
    reserved = _RESERVED_PATHS.get(kind, set())
    handle: str | None = None
    if kind == "youtube":
        if first.startswith("@"):
            handle = first[1:]
        elif first in {"c", "user"} and len(parts) > 1:
            handle = parts[1]
        elif first == "channel" and len(parts) > 1:
            handle = "channel:" + parts[1]
        elif first.lower() not in reserved:
            handle = first
    elif kind in {"tiktok", "threads"}:
        handle = first[1:] if first.startswith("@") else (None if first in reserved else first)
    elif kind == "instagram" and first == "stories" and len(parts) > 1:
        handle = parts[1]
    elif kind == "snapchat":
        handle = parts[1] if first == "add" and len(parts) > 1 else None
    elif kind == "reddit":
        handle = parts[1] if first in {"u", "user"} and len(parts) > 1 else None
    elif kind == "facebook" and first == "profile.php":
        fid = parse_qs(parsed.query).get("id", [""])[0]
        handle = f"id:{fid}" if fid else None
    elif kind == "spotify":
        handle = f"{first}:{parts[1]}" if len(parts) > 1 else None
    else:
        handle = None if first.lower() in reserved else first

    if not handle:
        return None
    handle = handle.lower().strip()
    if kind in {"twitch", "kick"}:
        handle = canonical_platform_handle(kind, handle)
    if not _HANDLE_OK.match(handle.replace(":", "")):
        return None
    unique = handle not in _GENERIC_IDENTITIES
    return SocialIdentity(kind, handle, clean_url, unique, via)


def extract_identities(profile: Profile) -> list[SocialIdentity]:
    found: dict[tuple[str, str], SocialIdentity] = {}
    texts = [t for t in (profile.description, profile.extra_bio) if t]
    for url in profile.extra_links:
        ident = normalize_social_url(url, via="profile_field")
        if ident:
            found.setdefault(ident.key(), ident)
    for text in texts:
        for url in extract_urls(text):
            ident = normalize_social_url(url)
            if ident:
                found.setdefault(ident.key(), ident)
        for kind, pattern in _MENTION_PATTERNS:
            for m in pattern.finditer(text):
                handle = m.group(1).strip("._-").lower()
                if len(handle) < 2 or handle in {"com", "tv", "gg"}:
                    continue
                # "twitch.tv/foo" is already handled as a URL; ignore domain fragments
                if "." in handle and kind in {"twitch", "kick", "x"}:
                    continue
                handle = canonical_platform_handle(kind, handle)
                ident = SocialIdentity(
                    kind,
                    handle,
                    f"mention:{m.group(0).strip()}",
                    handle not in _GENERIC_IDENTITIES,
                    "mention",
                )
                found.setdefault(ident.key(), ident)
    # Self-links (a Kick profile linking kick.com/<itself>) are not cross-platform evidence.
    self_key = (profile.platform, canonical_platform_handle(profile.platform, profile.username))
    found.pop(self_key, None)
    return list(found.values())


def _website_is_personal(domain: str, names: list[str]) -> bool:
    label = normalize_username(fold_accents(domain.split(".")[-2] if domain.count(".") >= 1 else domain))
    for n in names:
        nn = normalize_username(n)
        if len(nn) >= 4 and (nn in label or (len(label) >= 4 and label in nn)):
            return True
    return False


FrequencyFn = Callable[[str, str, set[tuple[str, str]]], int]


class SocialLinkMatcher:
    """Emits two signals: ``cross_link`` (LINK family) and ``shared_socials`` (SOCIAL family)."""

    def __init__(
        self, config: ScoringConfig, frequency: FrequencyFn | None = None, generic_min_accounts: int = 3
    ):
        self.config = config
        self.frequency = frequency
        self.generic_min_accounts = generic_min_accounts

    def compare(
        self,
        source: Profile,
        candidate: Profile,
        source_ids: list[SocialIdentity] | None = None,
        candidate_ids: list[SocialIdentity] | None = None,
    ) -> list[Signal]:
        s_ids = source_ids if source_ids is not None else extract_identities(source)
        c_ids = candidate_ids if candidate_ids is not None else extract_identities(candidate)
        return [
            self._cross_link(source, candidate, s_ids, c_ids),
            self._shared(source, candidate, s_ids, c_ids),
        ]

    # --- explicit cross-platform links -------------------------------------------
    def _cross_link(
        self, source: Profile, candidate: Profile, s_ids: list[SocialIdentity], c_ids: list[SocialIdentity]
    ) -> Signal:
        tgt, src = candidate.platform, source.platform
        cand_handle = canonical_platform_handle(tgt, candidate.username)
        src_handle = canonical_platform_handle(src, source.username)
        s_to_t = {i.identity for i in s_ids if i.kind == tgt}
        c_to_s = {i.identity for i in c_ids if i.kind == src}
        forward = cand_handle in s_to_t
        backward = src_handle in c_to_s
        data = {
            "source_links_to_target": sorted(s_to_t),
            "candidate_links_to_source": sorted(c_to_s),
            "forward": forward,
            "backward": backward,
        }
        if forward or backward:
            pts = self.config.cross_link_weight + (5.0 if forward and backward else 0.0)
            which = " and ".join(
                x
                for x in (
                    f"{src} profile links to {tgt}/{candidate.username}" if forward else "",
                    f"{tgt} profile links to {src}/{source.username}" if backward else "",
                )
                if x
            )
            return Signal(
                "cross_link", Family.LINK, True, 1.0, pts, Strength.STRONG, f"Explicit link: {which}", data
            )
        conflicts = []
        if s_to_t:
            conflicts.append(
                f"{src} profile links to a different {tgt} account ({', '.join(sorted(s_to_t))})"
            )
        if c_to_s:
            conflicts.append(
                f"{tgt} profile links to a different {src} account ({', '.join(sorted(c_to_s))})"
            )
        if conflicts:
            return Signal(
                "cross_link",
                Family.LINK,
                True,
                0.0,
                -self.config.link_conflict_penalty,
                Strength.CONFLICT,
                "; ".join(conflicts),
                data,
            )
        return Signal(
            "cross_link", Family.LINK, False, None, 0.0, Strength.NONE, "no cross-platform links", data
        )

    # --- shared external identities ---------------------------------------------
    def _is_unique(self, ident: SocialIdentity, names: list[str], pair: set[tuple[str, str]]) -> bool:
        if not ident.unique:
            return False
        if ident.kind == "website" and not _website_is_personal(ident.identity, names):
            return False
        if self.frequency is not None:
            try:
                if self.frequency(ident.kind, ident.identity, pair) >= self.generic_min_accounts:
                    return False
            except Exception:
                pass
        return True

    def _shared(
        self, source: Profile, candidate: Profile, s_ids: list[SocialIdentity], c_ids: list[SocialIdentity]
    ) -> Signal:
        skip = {"twitch", "kick"}
        s_map = {i.key(): i for i in s_ids if i.kind not in skip}
        c_map = {i.key(): i for i in c_ids if i.kind not in skip}
        if not s_map or not c_map:
            return Signal(
                "shared_socials",
                Family.SOCIAL,
                False,
                None,
                0.0,
                Strength.NONE,
                "social links unavailable on "
                + ("both profiles" if not s_map and not c_map else ("source" if not s_map else "candidate")),
                {
                    "source": [i.to_dict() for i in s_map.values()],
                    "candidate": [i.to_dict() for i in c_map.values()],
                },
            )
        names = [
            n for n in (source.username, source.display_name, candidate.username, candidate.display_name) if n
        ]
        pair = {(source.platform, source.username), (candidate.platform, candidate.username)}
        shared_keys = set(s_map) & set(c_map)
        unique_shared = sorted(k for k in shared_keys if self._is_unique(s_map[k], names, pair))
        weak_shared = sorted(k for k in shared_keys if k not in unique_shared)
        data: dict[str, object] = {
            "shared_unique": [f"{k}:{v}" for k, v in unique_shared],
            "shared_generic": [f"{k}:{v}" for k, v in weak_shared],
            "source": [i.to_dict() for i in s_map.values()],
            "candidate": [i.to_dict() for i in c_map.values()],
        }
        for kind in ("instagram", "youtube", "x", "tiktok"):
            data[f"{kind}_match"] = any(k == kind for k, _ in unique_shared)
        if unique_shared:
            pts = self.config.social_link_weight + self.config.social_additional_weight * min(
                2, len(unique_shared) - 1
            )
            return Signal(
                "shared_socials",
                Family.SOCIAL,
                True,
                1.0,
                pts,
                Strength.STRONG,
                "Same external account(s): " + ", ".join(f"{k} {v}" for k, v in unique_shared),
                data,
            )
        if weak_shared:
            return Signal(
                "shared_socials",
                Family.SOCIAL,
                True,
                0.3,
                min(4.0, 2.0 * len(weak_shared)),
                Strength.WEAK,
                "Only generic links shared (e.g. Discord/sponsor): "
                + ", ".join(f"{k} {v}" for k, v in weak_shared),
                data,
            )
        s_kinds = {k for k, _ in s_map if k not in {"website", "discord"}}
        c_kinds = {k for k, _ in c_map if k not in {"website", "discord"}}
        clash = sorted(s_kinds & c_kinds)
        if clash:
            return Signal(
                "shared_socials",
                Family.SOCIAL,
                True,
                0.0,
                -3.0,
                Strength.NEGATIVE,
                "Different accounts on the same network(s): " + ", ".join(clash),
                data,
            )
        return Signal(
            "shared_socials", Family.SOCIAL, True, 0.0, 0.0, Strength.NONE, "no shared social accounts", data
        )
