import httpx
from openpyxl import load_workbook
from openpyxl.styles import Font

from app.cache.service import CacheService
from app.excel.exporter import export_processed
from app.excel.importer import analyze_workbook
from app.excel.verifier import verify_output
from app.matching.candidates import CandidateGenerator, name_variants
from app.matching.social import extract_identities
from app.platforms.base import Profile
from app.platforms.http import ClientCredentialsToken, PlatformHttpClient, RetryPolicy
from app.platforms.kick import KickAdapter
from app.platforms.search_engine import handle_from_url
from app.platforms.twitch import TwitchAdapter
from app.services.stores import DbImageFeatureStore, ProfileStore
from tests.excel_helpers import KICK_HEADERS, make_workbook
from tests.fakes import FakePlatforms


def test_name_variants_cover_common_transformations():
    v = set(name_variants("davidsantos-"))
    assert {"davidsantos_", "davidsantos", "davidsantos_oficial", "davidsantostv"} <= v
    assert "ayzohtv" in set(name_variants("ayzoh"))
    assert "quentin_cey" in set(name_variants("quentin-cey"))
    assert "josepena" in set(name_variants("José-Peña"))


def test_handle_from_search_result_urls():
    assert handle_from_url("kick", "https://kick.com/bobinice76") == "bobinice76"
    assert handle_from_url("kick", "https://kick.com/categories/just-chatting") is None
    assert handle_from_url("twitch", "https://www.twitch.tv/NikkLive_/videos") == "nikklive_"
    assert handle_from_url("twitch", "https://twitch.tv/directory") is None
    assert handle_from_url("twitch", "https://example.com/foo") is None


def _twitch(fake: FakePlatforms) -> TwitchAdapter:
    client = httpx.AsyncClient(transport=fake.transport())
    token = ClientCredentialsToken(
        platform="twitch",
        token_url="https://id.twitch.tv/oauth2/token",
        client_id="i",
        client_secret="s",
        client=client,
    )
    http = PlatformHttpClient(
        platform="twitch",
        base_url="https://api.twitch.tv/helix",
        client=client,
        token=token,
        auth_headers=lambda t: {"Authorization": f"Bearer {t}", "Client-Id": "i"},
        retry=RetryPolicy(1, 0, 0),
        requests_per_minute=10**6,
    )
    return TwitchAdapter(http, CacheService(None), client)


async def test_generator_stages_and_rejections():
    fake = FakePlatforms()
    fake.add_twitch("linkedaccount")  # only reachable via explicit link
    fake.add_twitch("creator_tv")  # variant
    fake.add_twitch("kreator")  # search hit
    for i in range(30):
        fake.add_twitch(f"creator{i}x")  # noise from search
    src = Profile("kick", "creator", description="twitch.tv/linkedaccount")
    gen = CandidateGenerator(max_candidates=5)
    refs = await gen.generate(src, _twitch(fake), extract_identities(src))
    by = {r.handle: r.via for r in refs}
    assert "explicit_link" in by["linkedaccount"]
    assert "username_variant" in by["creator_tv"]
    assert len(refs) <= 5
    assert refs[0].handle == "linkedaccount"  # explicit links always first
    refs2 = await gen.generate(src, _twitch(fake), extract_identities(src), rejected={"creator_tv"})
    assert "creator_tv" not in {r.handle for r in refs2}


async def test_kick_has_no_search_so_generator_only_probes():
    fake = FakePlatforms()
    fake.add_kick("somecreator-tv")
    client = httpx.AsyncClient(transport=fake.transport())
    token = ClientCredentialsToken(
        platform="kick",
        token_url="https://id.kick.com/oauth/token",
        client_id="i",
        client_secret="s",
        client=client,
    )
    http = PlatformHttpClient(
        platform="kick",
        base_url="https://api.kick.com/public/v1",
        client=client,
        token=token,
        retry=RetryPolicy(1, 0, 0),
        requests_per_minute=10**6,
    )
    kick = KickAdapter(http, CacheService(None), client)
    refs = await CandidateGenerator().generate(Profile("twitch", "somecreator_tv"), kick, [])
    assert [r.handle for r in refs] == ["somecreator-tv"]
    assert fake.calls["api.kick.com/public/v1/channels"] == 1  # all variants in ONE batched request


def test_social_frequency_from_db(sf):
    store = ProfileStore(sf)
    for i in range(4):
        p = Profile("twitch", f"member{i}", description="instagram.com/bigteam")
        store.upsert(p, extract_identities(p))
    assert store.social_frequency("instagram", "bigteam", {("twitch", "member0")}) == 3
    assert store.social_frequency("instagram", "nobody", set()) == 0


def test_db_image_store_counts_distinct_accounts(sf):
    store = DbImageFeatureStore(sf)
    feats = {
        "sha256": "x",
        "variants": {"full": {"p": "ff00ff00ff00ff00", "d": "0", "a": "0"}},
        "low_complexity": False,
    }
    store.put("https://a/1", "kick", "a", feats, None)
    store.put("https://a/2", "kick", "a", feats, None)  # same account, new URL
    store.put("https://b/1", "twitch", "b", feats, None)
    assert store.accounts_sharing_hash("ff00ff00ff00ff00", set()) == 2
    assert store.get("https://a/1") == feats


def test_verifier_detects_formatting_change(tmp_path):
    src = make_workbook(tmp_path / "f.xlsx", KICK_HEADERS, [["abc", "France", None, None]])
    a = analyze_workbook(src)
    out = tmp_path / "f_processed.xlsx"
    expected = export_processed(src, out, a.columns, "kick", [])
    wb = load_workbook(out)
    wb.active["A1"].font = Font(bold=False)
    wb.save(out)
    rep = verify_output(src, out, a.columns, "kick", expected, [(2, "abc")])
    assert not rep.ok and any("formatting changed" in m for m in rep.mismatches)
