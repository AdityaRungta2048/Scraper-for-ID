"""Scoring configuration (weights and thresholds), sourced from Settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.config import Settings, get_settings


@dataclass(frozen=True)
class ScoringConfig:
    match_threshold: float = 90.0
    review_threshold: float = 70.0
    confidence_scale: float = 16.0
    cross_link_weight: float = 30.0
    social_link_weight: float = 25.0
    social_additional_weight: float = 8.0
    image_weight: float = 20.0
    username_weight: float = 15.0
    display_name_weight: float = 10.0
    name_family_cap: float = 20.0
    bio_weight: float = 10.0
    content_weight: float = 5.0
    country_weight: float = 2.0
    image_similarity_threshold: float = 0.90
    image_moderate_threshold: float = 0.80
    image_different_threshold: float = 0.62
    image_different_penalty: float = 6.0
    generic_image_factor: float = 0.15
    generic_image_min_accounts: int = 3
    link_conflict_penalty: float = 40.0

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ScoringConfig:
        s = settings or get_settings()
        return cls(**{k: getattr(s, k) for k in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
