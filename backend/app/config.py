"""Application settings. Every tunable is an environment variable (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- infrastructure -------------------------------------------------------------
    database_url: str = "sqlite:///./data/matcher.db"
    redis_url: str = "redis://localhost:6379/0"
    queue_backend: Literal["inline", "rq"] = "inline"
    rq_queue_name: str = "matcher"
    data_dir: Path = Path("./data")
    max_upload_mb: int = 25
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"
    log_json: bool = True

    # --- credentials (never sent to the frontend, never logged) ----------------------
    twitch_client_id: str = ""
    twitch_client_secret: SecretStr = SecretStr("")
    kick_client_id: str = ""
    kick_client_secret: SecretStr = SecretStr("")
    brave_search_api_key: SecretStr = SecretStr("")

    # --- endpoints (overridable for testing / future API moves) ----------------------
    twitch_api_base: str = "https://api.twitch.tv/helix"
    twitch_token_url: str = "https://id.twitch.tv/oauth2/token"
    kick_api_base: str = "https://api.kick.com/public/v1"
    kick_token_url: str = "https://id.kick.com/oauth/token"
    kick_public_site_base: str = "https://kick.com"
    brave_search_url: str = "https://api.search.brave.com/res/v1/web/search"

    # --- optional discovery / enrichment ---------------------------------------------
    kick_public_profile_enrichment: bool = False
    search_engine_fallback: bool = True  # only active when a search API key is configured
    bio_embeddings_enabled: bool = False  # requires the `ml` extra
    bio_embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # --- HTTP behaviour --------------------------------------------------------------
    api_concurrency: int = 4
    twitch_requests_per_minute: int = 600
    kick_requests_per_minute: int = 120
    search_requests_per_minute: int = 30
    retry_count: int = 4
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0
    http_timeout: float = 15.0
    image_max_bytes: int = 5 * 1024 * 1024
    image_allowed_hosts: str = (
        "static-cdn.jtvnw.net,files.kick.com,kick.com,stream.kick.com,images.kick.com,"
        "dbxmjjzl5pc1g.cloudfront.net"
    )

    # --- job processing --------------------------------------------------------------
    row_concurrency: int = 4
    existing_destination_policy: Literal["preserve", "overwrite"] = "preserve"

    # --- caching (seconds) -----------------------------------------------------------
    cache_ttl: int = 24 * 3600
    negative_cache_ttl: int = 6 * 3600
    resolution_cache_ttl: int = 3 * 24 * 3600
    image_cache_ttl: int = 7 * 24 * 3600

    # --- candidate generation --------------------------------------------------------
    max_candidates: int = 15
    search_results_per_query: int = 10
    image_max_candidates: int = 6

    # --- scoring (see docs/DESIGN.md, section F) --------------------------------------
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

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def twitch_configured(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret.get_secret_value())

    @property
    def kick_configured(self) -> bool:
        return bool(self.kick_client_id and self.kick_client_secret.get_secret_value())

    @property
    def search_engine_configured(self) -> bool:
        return bool(self.search_engine_fallback and self.brave_search_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
