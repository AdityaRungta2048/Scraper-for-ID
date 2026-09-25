"""Test dataset (spec §72): difficult identity situations, as a fake Twitch/Kick world.

Every expected value below is what a careful human would conclude from the evidence
the fake world exposes; nothing is hard-coded in the application.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from tests.fakes import FakePlatforms, distinctive_image, generic_image, platform_variant, to_bytes


@dataclass
class Expect:
    source: str | None
    country: str
    dest: str | None
    remarks: str | None
    status: str  # row status
    case: str  # what the row exercises (spec §72 item)


def img(seed: int) -> bytes:
    return to_bytes(distinctive_image(seed))


def img_v(seed: int) -> bytes:
    return platform_variant(distinctive_image(seed))


def _slug_request(slug: str):
    def pred(req: httpx.Request) -> bool:
        return req.url.path == "/public/v1/channels" and req.url.params.get_list("slug") == [slug]

    return pred


def build_kick_world(f: FakePlatforms) -> list[Expect]:
    # 1/6/7/12/13  exact same username, same person, same (re-encoded) picture, no social links
    f.add_kick("starwraith", "StarWraith", image=img(1), category="Elden Ring", language="Spanish")
    f.add_twitch("starwraith", "StarWraith", image=img_v(1), game="Elden Ring", language="es")
    # 2/14/16  exact same username, different people, different countries/games/socials
    f.add_kick(
        "alex123",
        description="Follow instagram.com/alex_kick",
        image=img(2),
        category="Fortnite",
        language="French",
    )
    f.add_twitch("alex123", description="IG: @alex_twitch", image=img(3), game="Minecraft", language="pt")
    # 3/9  similar usernames, same person, same Instagram, found via platform search
    f.add_kick(
        "nikkilve",
        "NikkiLve",
        description="Insta: @nikki.live.de",
        image=img(4),
        category="VALORANT",
        language="German",
    )
    f.add_twitch(
        "NikkLive_",
        "NikkLive_",
        description="instagram.com/nikki.live.de",
        image=img_v(4),
        game="VALORANT",
        language="de",
    )
    # 4  similar usernames, different people
    f.add_kick("fekah", image=img(5))
    f.add_twitch("fekah_", "Fekah_", image=img(6))
    # 5/18/21  completely different usernames, same person, explicit mutual links
    f.add_kick(
        "the-real-abc", "the_real_abc", description="Also live on twitch.tv/ABCOfficialTV", image=img(7)
    )
    f.add_twitch("abcofficialtv", "ABCOfficialTV", description="Kick: kick.com/the-real-abc", image=img_v(7))
    # 8  generic profile image + same username => never an automatic match
    f.add_kick("genericguy", image=to_bytes(generic_image()))
    f.add_twitch("genericguy", image=to_bytes(generic_image()))
    # 10  same YouTube + same picture, suffixed handle
    f.add_kick("ytcreator", description="youtube.com/@YTCreatorChannel", image=img(8))
    f.add_twitch(
        "ytcreator_tv",
        "YTCreator_TV",
        description="https://www.youtube.com/@ytcreatorchannel",
        image=img_v(8),
    )
    # 11  same personal website
    f.add_kick("sitecreator", description="Boutique: www.sitecreator.fr", image=img(9))
    f.add_twitch("site_creator", "site_creator", description="https://sitecreator.fr/", image=img_v(9))
    # 17  same display name, different person
    f.add_kick("bluefox", "BlueFox", image=img(10))
    f.add_twitch("bluefoxx", "BlueFox", image=img(11))
    # 19  account not found (both / twitch only / renamed with back-link)
    f.add_twitch("ghostkick", image=img(12))
    f.add_twitch("renamedold", "RenamedOld", description="Moved! kick.com/renamedold", image=img(30))
    # 20  persistent API failure for one row
    f.fail(_slug_request("flaky"), status=503)
    # 26  accented display name / hyphenated slug
    f.add_kick("jose-pena", "JoséPeña", image=img(13))
    f.add_twitch("josepena", "JosePena", image=img_v(13))
    # 27  hyphen vs underscore + same Instagram
    f.add_kick("quentin-cey", "quentin_cey", description="IG: @quentincey", image=img(14))
    f.add_twitch("quentin_cey", description="instagram.com/quentincey", image=img_v(14))
    # 28  TV suffix + same TikTok
    f.add_kick("ayzoh", description="tiktok.com/@ayzoh", image=img(15))
    f.add_twitch("ayzohtv", "ayzohTV", description="TikTok: @ayzoh", image=img_v(15))
    # 29  Live suffix, different person
    f.add_kick("marco", image=img(16))
    f.add_twitch("marcolive", image=img(17))
    # 30  Official suffix + same Instagram
    f.add_kick("davidsantos-", "davidsantos_", description="instagram.com/davidsantos.oficial", image=img(18))
    f.add_twitch("davidsantos_oficial", description="IG: @davidsantos.oficial", image=img_v(18))
    # ambiguity: two twitch accounts with the same picture
    f.add_kick("twinz", image=img(19))
    f.add_twitch("twinz", image=img_v(19))
    f.add_twitch("twinz_tv", image=img_v(19))
    # conflicting explicit link
    f.add_kick("conflicted", description="my twitch: twitch.tv/someoneelse", image=img(20))
    f.add_twitch("conflicted", image=img_v(20))
    f.add_twitch("someoneelse", image=img(21))
    # abc vs abcgaming (suffix must not merge distinct people)
    f.add_kick("abc", image=img(22))
    f.add_twitch("abcgaming", image=img(23))
    # 21/22 rate limit and timeout, recovered by retries
    f.add_kick("ratelimited", image=img(24))
    f.add_twitch("ratelimited", "RateLimited", image=img_v(24))
    f.fail(_slug_request("ratelimited"), status=429, times=1, headers={"Retry-After": "0"})
    f.add_kick("timeoutguy", image=img(25))
    f.add_twitch("timeoutguy", image=img_v(25))
    f.fail(_slug_request("timeoutguy"), exc=httpx.ReadTimeout, times=1)

    return [
        Expect("starwraith", "Spain", "StarWraith", None, "MATCH", "exact username same person"),
        Expect("alex123", "France", None, "nearest possible channel", "*", "exact username different people"),
        Expect("nikkilve", "Germany", "NikkLive_", None, "MATCH", "similar username same person"),
        Expect(
            "fekah", "France", None, "nearest possible channel", "*", "similar usernames different people"
        ),
        Expect("the-real-abc", "France", "ABCOfficialTV", None, "MATCH", "different usernames same person"),
        Expect("genericguy", "Italy", None, "nearest possible channel", "*", "generic profile image"),
        Expect("ytcreator", "Spain", "YTCreator_TV", None, "MATCH", "same YouTube"),
        Expect("sitecreator", "France", "site_creator", None, "MATCH", "same website"),
        Expect(
            "bluefox", "Germany", None, "nearest possible channel", "*", "same display name different person"
        ),
        Expect(
            "unknownabc", "France", None, "no Id on both platforms", "SOURCE_NOT_FOUND", "not found anywhere"
        ),
        Expect(
            "ghostkick", "France", None, "no kick id", "SOURCE_NOT_FOUND", "kick missing, twitch unverified"
        ),
        Expect(
            "renamedold", "Germany", "RenamedOld", "no kick id", "MATCH", "renamed: twitch links old kick id"
        ),
        Expect("flaky", "France", None, None, "TEMPORARY_ERROR", "API failure"),
        Expect(None, "France", None, None, "SKIPPED_EMPTY", "missing source id"),
        Expect("starwraith", "Spain", "StarWraith", None, "MATCH", "duplicate row"),
        Expect("jose-pena", "Spain", "JosePena", None, "MATCH", "accented name"),
        Expect("quentin-cey", "France", "quentin_cey", None, "MATCH", "hyphen vs underscore"),
        Expect("ayzoh", "France", "ayzohTV", None, "MATCH", "TV suffix"),
        Expect("marco", "Italy", None, "nearest possible channel", "*", "live suffix different person"),
        Expect("davidsantos-", "Spain", "davidsantos_oficial", None, "MATCH", "official suffix"),
        Expect("twinz", "France", None, "nearest possible channel", "REVIEW", "ambiguous candidates"),
        Expect(
            "conflicted", "France", None, "nearest possible channel", "REVIEW", "conflicting explicit link"
        ),
        Expect("abc", "France", None, "nearest possible channel", "*", "abc vs abcgaming"),
        Expect("ratelimited", "Spain", "RateLimited", None, "MATCH", "rate limit recovered"),
        Expect("timeoutguy", "Spain", "timeoutguy", None, "MATCH", "timeout recovered"),
    ]


def build_twitch_world(f: FakePlatforms) -> list[Expect]:
    f.add_twitch("bobinice7676", description="insta: @bobinice", image=img(40))
    f.add_kick("bobinice76", description="instagram.com/bobinice", image=img_v(40))
    f.brave['site:kick.com "bobinice7676"'] = ["https://kick.com/bobinice76", "https://kick.com/categories"]
    f.add_twitch("starwraith", "StarWraith", image=img(41))
    f.add_kick("starwraith", image=img_v(41))
    f.add_twitch("kosstochka", image=img(42))
    f.add_twitch("jimmyboyyy", image=img(43))
    f.add_kick("jimmyboy", image=img(44))
    f.add_twitch("fierik", default_avatar=True)
    f.add_kick("fierik", image=to_bytes(generic_image((83, 252, 24))))
    f.add_kick("kal-75020yt", image=img(45))
    f.add_twitch("juakynen", description="También en kick.com/juaky", image=img(46))
    f.add_kick("juaky", image=img_v(46))
    f.add_twitch("nyaneila", description="tiktok.com/@nyaneila", image=img(47))
    f.add_kick("nyaneilatv", description="TikTok: @nyaneila", image=img_v(47))
    return [
        Expect("bobinice7676", "France", "bobinice76", None, "MATCH", "search-engine discovery"),
        Expect("kosstochka", "Italy", None, "no kick id", "NO_MATCH", "twitch exists, no kick"),
        Expect("starwraith", "Spain", "starwraith", None, "MATCH", "exact username same person"),
        Expect("unknowntw", "Italy", None, "no Id on both platforms", "SOURCE_NOT_FOUND", "none"),
        Expect("jimmyboyyy", "France", None, "no kick id", "*", "different person; no kick candidate found"),
        Expect(
            "fierik", "Italy", None, "nearest possible channel", "*", "generic avatars; kick fierik linked"
        ),
        Expect(
            "kal-75020yt", "France", None, "no twitch id found", "SOURCE_NOT_FOUND", "invalid twitch login"
        ),
        Expect("juakynen", "Spain", "juaky", None, "MATCH", "explicit link"),
        Expect("nyaneila", "Germany", "nyaneilatv", None, "MATCH", "tv suffix + tiktok"),
        Expect(None, "Spain", None, None, "SKIPPED_EMPTY", "empty"),
    ]
