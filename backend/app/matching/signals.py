"""Evidence model shared by all matchers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Strength(StrEnum):
    STRONG = "strong"  # independent identity evidence (link, unique social, non-generic image)
    SUPPORTING = "supporting"  # corroborates, cannot establish identity alone
    WEAK = "weak"
    NONE = "none"
    NEGATIVE = "negative"  # evidence against
    CONFLICT = "conflict"  # hard contradiction


class Family(StrEnum):
    LINK = "link"
    SOCIAL = "social"
    IMAGE = "image"
    NAME = "name"
    TEXT = "text"
    CONTENT = "content"
    COUNTRY = "country"


@dataclass
class Signal:
    name: str
    family: Family
    available: bool
    score: float | None = None  # 0..1 similarity (None if not computable)
    points: float = 0.0  # contribution to the evidence total (negative allowed)
    strength: Strength = Strength.NONE
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["family"] = self.family.value
        d["strength"] = self.strength.value
        if self.score is not None:
            d["score"] = round(self.score, 4)
        d["points"] = round(self.points, 2)
        return d


def unavailable(name: str, family: Family, detail: str) -> Signal:
    return Signal(name=name, family=family, available=False, detail=detail)
