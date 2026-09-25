"""Profile-image analysis: normalisation + perceptual hashing + generic-avatar detection.

Never compares URLs. Images are downloaded (allow-listed CDNs only), normalised
(first frame, alpha composited on white, uniform borders trimmed, square centre crop,
resized) and hashed with pHash/dHash/aHash on a full and a centre-cropped variant, so
resizing, recompression, PNG↔JPEG, borders and moderate crops are tolerated.

Generic images (platform default avatars, low-complexity images, or an image hash
shared by several unrelated accounts) get very little weight and never count as
strong evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import imagehash
import numpy as np
from PIL import Image, ImageChops, ImageOps

from app.matching.config import ScoringConfig
from app.matching.signals import Family, Signal, Strength, unavailable
from app.platforms.base import PlatformAdapter, Profile

DEFAULT_AVATAR_MARKERS = (
    "user-default-pictures",
    "default-avatar",
    "/default/",
    "default_profile",
    "placeholder",
)
_HASH_WEIGHTS = {"p": 0.5, "d": 0.3, "a": 0.2}
NORMALISED_SIZE = 256


def _load(data: bytes) -> Image.Image:
    opened = Image.open(io.BytesIO(data))
    opened.seek(0)
    img: Image.Image = ImageOps.exif_transpose(opened)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return img.convert("RGB")


def _trim_border(img: Image.Image, tolerance: int = 18) -> Image.Image:
    corner = img.getpixel((0, 0))
    bg = Image.new("RGB", img.size, corner)  # type: ignore[arg-type]
    diff = ImageChops.difference(img, bg).convert("L").point(lambda v: 255 if v > tolerance else 0)
    bbox = diff.getbbox()
    if not bbox:
        return img
    w, h = img.size
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if bw * bh < 0.3 * w * h:  # do not trim away the subject
        return img
    return img.crop(bbox)


def _square(img: Image.Image, fraction: float = 1.0) -> Image.Image:
    w, h = img.size
    side = int(min(w, h) * fraction)
    left, top = (w - side) // 2, (h - side) // 2
    return img.crop((left, top, left + side, top + side)).resize(
        (NORMALISED_SIZE, NORMALISED_SIZE), Image.Resampling.LANCZOS
    )


def _hashes(img: Image.Image) -> dict[str, str]:
    return {
        "p": str(imagehash.phash(img)),
        "d": str(imagehash.dhash(img)),
        "a": str(imagehash.average_hash(img)),
    }


def _low_complexity(img: Image.Image) -> bool:
    small = img.resize((64, 64))
    arr = np.asarray(small.convert("L"), dtype=np.float32)
    if float(arr.std()) < 12.0:
        return True
    quant = small.quantize(colors=32)
    counts = sorted((c for c, _ in (quant.getcolors() or [])), reverse=True)
    total = sum(counts) or 1
    significant = [c for c in counts if c / total >= 0.02]
    return len(significant) <= 3


def compute_features(data: bytes) -> dict[str, Any]:
    """Hash several normalised variants. Border trimming is ambiguous (a frame added by a
    platform vs. the picture's own uniform background), so both the untrimmed and the
    trimmed picture are hashed and comparisons take the best-aligned pair."""
    raw = _load(data)
    trimmed = _trim_border(raw)
    full = _square(raw, 1.0)
    variants = {"full": _hashes(full), "center": _hashes(_square(raw, 0.8))}
    if trimmed.size != raw.size:
        variants["trim"] = _hashes(_square(trimmed, 1.0))
        variants["trim_center"] = _hashes(_square(trimmed, 0.8))
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "variants": variants,
        "low_complexity": _low_complexity(full),
    }


def _hamming(h1: str, h2: str) -> int:
    return int(imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2))


def hash_similarity(fa: dict[str, Any], fb: dict[str, Any]) -> float:
    if fa.get("sha256") and fa.get("sha256") == fb.get("sha256"):
        return 1.0
    best = 0.0
    for va in fa["variants"].values():
        for vb in fb["variants"].values():
            dist = sum(w * _hamming(va[k], vb[k]) for k, w in _HASH_WEIGHTS.items()) / 64.0
            best = max(best, 1.0 - dist)
    return best


def is_default_avatar_url(url: str | None) -> bool:
    return not url or any(m in url.lower() for m in DEFAULT_AVATAR_MARKERS)


class ImageFeatureStore(Protocol):
    def get(self, url: str) -> dict[str, Any] | None: ...
    def put(
        self, url: str, platform: str, username: str, features: dict[str, Any] | None, error: str | None
    ) -> None: ...
    def accounts_sharing_hash(self, phash: str, exclude: set[tuple[str, str]]) -> int: ...


class InMemoryFeatureStore:
    def __init__(self) -> None:
        self._by_url: dict[str, dict[str, Any] | None] = {}
        self._owners: dict[str, set[tuple[str, str]]] = {}

    def get(self, url: str) -> dict[str, Any] | None:
        return self._by_url.get(url)

    def put(
        self, url: str, platform: str, username: str, features: dict[str, Any] | None, error: str | None
    ) -> None:
        self._by_url[url] = features
        if features:
            self._owners.setdefault(features["variants"]["full"]["p"], set()).add((platform, username))

    def accounts_sharing_hash(self, phash: str, exclude: set[tuple[str, str]]) -> int:
        return len(self._owners.get(phash, set()) - exclude)


@dataclass
class ImageInfo:
    features: dict[str, Any] | None
    generic: bool
    generic_reason: str | None
    error: str | None = None


class ProfileImageMatcher:
    name = "profile_image"

    def __init__(
        self,
        config: ScoringConfig,
        store: ImageFeatureStore | None = None,
        hasher: Callable[[bytes], dict[str, Any]] = compute_features,
    ) -> None:
        self.config = config
        self.store = store or InMemoryFeatureStore()
        self.hasher = hasher
        self._locks: dict[str, asyncio.Lock] = {}

    async def load(self, adapter: PlatformAdapter, profile: Profile) -> ImageInfo:
        url = profile.profile_image_url
        if is_default_avatar_url(url):
            return ImageInfo(None, True, "platform default avatar" if url else "no profile image")
        assert url is not None
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            features = self.store.get(url)
            if features is None:
                data = await adapter.get_image(url)  # may raise TemporaryPlatformError
                if data is None:
                    return ImageInfo(None, False, None, "image not retrievable")
                try:
                    features = await asyncio.to_thread(self.hasher, data)
                except Exception as exc:
                    self.store.put(
                        url, profile.platform, profile.username, None, f"decode: {type(exc).__name__}"
                    )
                    return ImageInfo(None, False, None, "image could not be decoded")
                self.store.put(url, profile.platform, profile.username, features, None)
        if features.get("low_complexity"):
            return ImageInfo(features, True, "low-complexity image (logo/solid colour)")
        return ImageInfo(features, False, None)

    def generic_by_frequency(self, info: ImageInfo, source: Profile, candidate: Profile) -> bool:
        if not info.features:
            return False
        exclude = {(source.platform, source.username), (candidate.platform, candidate.username)}
        phash = info.features["variants"]["full"]["p"]
        return self.store.accounts_sharing_hash(phash, exclude) >= self.config.generic_image_min_accounts

    def compare(self, source: Profile, candidate: Profile, si: ImageInfo, ci: ImageInfo) -> Signal:
        if not si.features or not ci.features:
            reason = si.generic_reason or si.error or ci.generic_reason or ci.error or "image unavailable"
            return unavailable(self.name, Family.IMAGE, f"cannot compare profile images: {reason}")
        sim = hash_similarity(si.features, ci.features)
        generic_reasons = [r for r in (si.generic_reason, ci.generic_reason) if r]
        if self.generic_by_frequency(si, source, candidate) or self.generic_by_frequency(
            ci, source, candidate
        ):
            generic_reasons.append("same image used by several unrelated accounts")
        generic = bool(generic_reasons)
        c = self.config
        data = {"similarity": round(sim, 4), "generic": generic, "generic_reasons": generic_reasons}
        if sim >= c.image_similarity_threshold:
            pts, strength, text = c.image_weight, Strength.STRONG, "same profile picture"
        elif sim >= c.image_moderate_threshold:
            pts, strength, text = (
                c.image_weight * 0.5,
                Strength.SUPPORTING,
                "very similar profile picture (crop/background change)",
            )
        elif sim < c.image_different_threshold:
            pts, strength, text = -c.image_different_penalty, Strength.NEGATIVE, "different profile pictures"
        else:
            pts, strength, text = 0.0, Strength.NONE, "profile pictures inconclusive"
        if generic:
            if pts > 0:
                pts *= c.generic_image_factor
                strength = Strength.WEAK
            else:
                pts, strength = 0.0, Strength.NONE
            text += " — generic image, low weight (" + "; ".join(generic_reasons) + ")"
        return Signal(self.name, Family.IMAGE, True, sim, pts, strength, f"{text} ({sim:.0%})", data)
