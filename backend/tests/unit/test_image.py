import io

import pytest
from PIL import Image

from app.matching.config import ScoringConfig
from app.matching.image import (
    ImageInfo,
    InMemoryFeatureStore,
    ProfileImageMatcher,
    compute_features,
    hash_similarity,
    is_default_avatar_url,
)
from app.matching.signals import Strength
from app.platforms.base import Profile
from tests.fakes import distinctive_image, generic_image, platform_variant, to_bytes

CFG = ScoringConfig()
A = Profile("kick", "a")
B = Profile("twitch", "b")


def feats(data: bytes):
    return compute_features(data)


def test_identical_bytes_similarity_one():
    data = to_bytes(distinctive_image(1))
    assert hash_similarity(feats(data), feats(data)) == 1.0


@pytest.mark.parametrize("transform", ["resize_jpeg_border", "png_to_jpeg", "small_crop", "color_shift"])
def test_same_picture_survives_platform_processing(transform):
    img = distinctive_image(7)
    if transform == "resize_jpeg_border":
        other = platform_variant(img)
    elif transform == "png_to_jpeg":
        other = to_bytes(img, "JPEG", quality=60)
    elif transform == "small_crop":
        other = to_bytes(img.crop((8, 8, 292, 292)))
    else:
        other = to_bytes(Image.eval(img, lambda v: min(255, int(v * 1.05))))
    sim = hash_similarity(feats(to_bytes(img)), feats(other))
    assert sim >= CFG.image_moderate_threshold, (transform, sim)


def test_different_pictures_low_similarity():
    sims = [
        hash_similarity(feats(to_bytes(distinctive_image(i))), feats(to_bytes(distinctive_image(i + 100))))
        for i in range(5)
    ]
    assert max(sims) < CFG.image_different_threshold + 0.1, sims


def test_low_complexity_flagged():
    assert feats(to_bytes(generic_image()))["low_complexity"] is True
    assert feats(to_bytes(distinctive_image(3)))["low_complexity"] is False


def test_default_avatar_urls():
    assert is_default_avatar_url("https://static-cdn.jtvnw.net/user-default-pictures-uv/x.png")
    assert is_default_avatar_url(None)
    assert not is_default_avatar_url("https://static-cdn.jtvnw.net/jtv_user_pictures/abc.png")


def _info(data: bytes, generic_reason=None):
    f = feats(data)
    return ImageInfo(
        f,
        bool(generic_reason) or f["low_complexity"],
        generic_reason or ("low-complexity" if f["low_complexity"] else None),
    )


def test_strong_match_signal():
    img = distinctive_image(11)
    sig = ProfileImageMatcher(CFG).compare(A, B, _info(to_bytes(img)), _info(platform_variant(img)))
    assert sig.strength == Strength.STRONG and sig.points == CFG.image_weight


def test_generic_similar_images_get_little_weight():
    sig = ProfileImageMatcher(CFG).compare(
        A, B, _info(to_bytes(generic_image())), _info(to_bytes(generic_image()))
    )
    assert sig.strength == Strength.WEAK
    assert sig.points <= CFG.image_weight * CFG.generic_image_factor + 1e-9


def test_different_images_negative_but_not_for_generic():
    m = ProfileImageMatcher(CFG)
    sig = m.compare(A, B, _info(to_bytes(distinctive_image(1))), _info(to_bytes(distinctive_image(2))))
    assert sig.strength == Strength.NEGATIVE and sig.points < 0
    sig2 = m.compare(A, B, _info(to_bytes(generic_image())), _info(to_bytes(distinctive_image(2))))
    assert sig2.points == 0


def test_image_shared_by_many_accounts_is_generic():
    store = InMemoryFeatureStore()
    data = to_bytes(distinctive_image(5))
    for i in range(4):
        store.put(f"https://x/{i}", "twitch", f"user{i}", feats(data), None)
    m = ProfileImageMatcher(CFG, store=store)
    sig = m.compare(A, B, _info(data), _info(data))
    assert "several unrelated accounts" in sig.detail
    assert sig.strength == Strength.WEAK


def test_corrupt_image_does_not_crash():
    with pytest.raises(Exception):  # noqa: B017
        compute_features(b"not an image")
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    assert compute_features(buf.getvalue())["low_complexity"] is True
