"""Wires adapters + matching engine together for one processing run."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.cache.service import CacheService
from app.config import Settings
from app.logging_setup import get_logger
from app.matching.candidates import CandidateGenerator
from app.matching.config import ScoringConfig
from app.matching.image import ProfileImageMatcher
from app.matching.text import SentenceTransformerEmbedder, TextEmbedder
from app.matching.verifier import IdentityVerifier
from app.platforms.registry import PlatformBundle, build_platforms
from app.services.resolver import IdentityResolver
from app.services.stores import DbImageFeatureStore, ProfileStore

log = get_logger("context")


# Test hook: integration tests route all platform HTTP through a fake transport.
_default_transport: httpx.AsyncBaseTransport | None = None


def set_default_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _default_transport
    _default_transport = transport


@dataclass
class MatchingContext:
    resolver: IdentityResolver
    platforms: PlatformBundle
    cache: CacheService

    async def aclose(self) -> None:
        await self.platforms.aclose()


def build_context(
    settings: Settings,
    session_factory: sessionmaker[Session],
    transport: httpx.AsyncBaseTransport | None = None,
) -> MatchingContext:
    cache = CacheService(session_factory)
    platforms = build_platforms(settings, cache, transport=transport or _default_transport)
    config = ScoringConfig.from_settings(settings)
    profile_store = ProfileStore(session_factory)
    embedder: TextEmbedder | None = None
    if settings.bio_embeddings_enabled:
        try:
            embedder = SentenceTransformerEmbedder(settings.bio_embedding_model)
        except Exception as exc:
            log.warning("bio_embeddings_unavailable", error=type(exc).__name__)
    verifier = IdentityVerifier(
        config,
        image_matcher=ProfileImageMatcher(
            config, DbImageFeatureStore(session_factory, settings.image_cache_ttl)
        ),
        social_frequency=profile_store.social_frequency,
        text_embedder=embedder,
        image_max_candidates=settings.image_max_candidates,
    )
    generator = CandidateGenerator(
        settings.max_candidates, settings.search_results_per_query, platforms.search_engine
    )
    resolver = IdentityResolver(
        {"twitch": platforms.twitch, "kick": platforms.kick},
        generator,
        verifier,
        cache,
        profile_store,
        settings.resolution_cache_ttl,
    )
    return MatchingContext(resolver=resolver, platforms=platforms, cache=cache)
