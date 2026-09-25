"""Persistence helpers used by the matching engine (accounts, social links, image hashes)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.cache.service import as_utc
from app.matching.social import SocialIdentity
from app.models import ImageHashRecord, PlatformAccount, SocialLink
from app.platforms.base import Profile


class ProfileStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.sf = session_factory

    def upsert(self, profile: Profile, identities: list[SocialIdentity]) -> None:
        with self.sf() as s:
            acc = s.execute(
                select(PlatformAccount).where(
                    PlatformAccount.platform == profile.platform,
                    PlatformAccount.username_key == profile.username,
                )
            ).scalar_one_or_none()
            if acc is None:
                acc = PlatformAccount(
                    platform=profile.platform, username=profile.username, username_key=profile.username
                )
                s.add(acc)
            acc.platform_user_id = profile.user_id
            acc.display_name = profile.display_name
            acc.profile_url = profile.profile_url
            acc.profile_image_url = profile.profile_image_url
            acc.description = profile.description
            acc.language = profile.language
            acc.category = profile.category
            acc.stream_title = profile.stream_title
            acc.tags = profile.tags
            acc.raw_json = profile.to_dict()
            acc.source = profile.source
            acc.last_seen = datetime.now(UTC)
            acc.social_links.clear()
            for ident in identities:
                acc.social_links.append(
                    SocialLink(
                        kind=ident.kind, identity=ident.identity, url=ident.url, unique_identity=ident.unique
                    )
                )
            s.commit()

    def social_frequency(self, kind: str, identity: str, exclude: set[tuple[str, str]]) -> int:
        """Number of distinct stored accounts (other than ``exclude``) linking this identity."""
        with self.sf() as s:
            q = (
                select(func.count(func.distinct(PlatformAccount.id)))
                .join(SocialLink, SocialLink.account_id == PlatformAccount.id)
                .where(SocialLink.kind == kind, SocialLink.identity == identity)
            )
            if exclude:
                q = q.where(
                    not_(
                        or_(
                            *[
                                and_(PlatformAccount.platform == p, PlatformAccount.username_key == u)
                                for p, u in exclude
                            ]
                        )
                    )
                )
            # a creator's own accounts on both platforms legitimately share identities; count
            # distinct *usernames* per platform to avoid treating that as "generic"
            return int(s.execute(q).scalar_one() or 0)


class DbImageFeatureStore:
    def __init__(self, session_factory: sessionmaker[Session], ttl_seconds: int = 7 * 86400) -> None:
        self.sf = session_factory
        self.ttl = ttl_seconds
        self._mem: dict[str, dict[str, Any] | None] = {}

    def get(self, url: str) -> dict[str, Any] | None:
        if url in self._mem:
            return self._mem[url]
        with self.sf() as s:
            rec = s.execute(select(ImageHashRecord).where(ImageHashRecord.url == url)).scalar_one_or_none()
            if rec is None or rec.features_json is None:
                return None
            if as_utc(rec.updated_at) < datetime.now(UTC) - timedelta(seconds=self.ttl):
                return None
            self._mem[url] = rec.features_json
            return rec.features_json

    def put(
        self, url: str, platform: str, username: str, features: dict[str, Any] | None, error: str | None
    ) -> None:
        self._mem[url] = features
        with self.sf() as s:
            rec = s.execute(select(ImageHashRecord).where(ImageHashRecord.url == url)).scalar_one_or_none()
            if rec is None:
                rec = ImageHashRecord(url=url)
                s.add(rec)
            rec.platform = platform
            rec.username_key = username
            rec.features_json = features
            rec.sha256 = features.get("sha256") if features else None
            rec.phash = features["variants"]["full"]["p"] if features else None
            rec.low_complexity = bool(features and features.get("low_complexity"))
            rec.fetch_error = error
            rec.updated_at = datetime.now(UTC)
            s.commit()

    def accounts_sharing_hash(self, phash: str, exclude: set[tuple[str, str]]) -> int:
        with self.sf() as s:
            rows = s.execute(
                select(ImageHashRecord.platform, ImageHashRecord.username_key).where(
                    ImageHashRecord.phash == phash
                )
            ).all()
        return len({(p, u) for p, u in rows if p and u} - exclude)
