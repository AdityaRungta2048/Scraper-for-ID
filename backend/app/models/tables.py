"""Persistent entities.

Row-level results are persisted individually (processing_rows) so jobs are resumable,
and every decision records the matching-engine version for auditability.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class JobStatus(enum.StrEnum):
    UPLOADED = "UPLOADED"  # validated, waiting for the user to start (or choose a platform)
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"  # finished, but some rows hit temporary errors (retryable)
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RowStatus(enum.StrEnum):
    PENDING = "PENDING"
    SOURCE_EXISTS = "SOURCE_EXISTS"  # transient: source verified, matching in progress
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    REVIEW = "REVIEW"
    API_ERROR = "API_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARY_ERROR = "TEMPORARY_ERROR"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    SKIPPED_EMPTY = "SKIPPED_EMPTY"
    PRESERVED = "PRESERVED"  # destination already filled in the upload; left untouched

    @property
    def is_error(self) -> bool:
        return self in ERROR_STATUSES

    @property
    def is_final(self) -> bool:
        return self in FINAL_STATUSES


ERROR_STATUSES = frozenset(
    {RowStatus.API_ERROR, RowStatus.RATE_LIMITED, RowStatus.TEMPORARY_ERROR, RowStatus.PROCESSING_ERROR}
)
FINAL_STATUSES = frozenset(
    {
        RowStatus.SOURCE_NOT_FOUND,
        RowStatus.MATCH,
        RowStatus.NO_MATCH,
        RowStatus.REVIEW,
        RowStatus.SKIPPED_EMPTY,
        RowStatus.PRESERVED,
    }
)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(1024))
    output_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    review_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_platform: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detected_platform: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detection_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    header_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    processed_rows: Mapped[int] = mapped_column(Integer, default=0)
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    no_match_count: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    not_found_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.UPLOADED.value, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    verification_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    export_stale: Mapped[bool] = mapped_column(Boolean, default=True)
    matching_engine_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    rows: Mapped[list[ProcessingRow]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ProcessingRow.original_row"
    )


class ProcessingRow(Base):
    __tablename__ = "processing_rows"
    __table_args__ = (
        UniqueConstraint("job_id", "original_row", name="uq_row_job_original_row"),
        Index("ix_rows_job_status", "job_id", "status"),
        Index("ix_rows_job_key", "job_id", "source_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"))
    # Immutable identity of the row: the 1-based Excel row number in the uploaded sheet.
    original_row: Mapped[int] = mapped_column(Integer)
    source_value: Mapped[str | None] = mapped_column(String(512), nullable=True)  # exact original text
    source_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # normalised handle
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    existing_destination: Mapped[str | None] = mapped_column(String(512), nullable=True)
    existing_remarks: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[str] = mapped_column(String(24), default=RowStatus.PENDING.value)
    source_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    target_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_candidate: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # What the exporter writes (None = leave the cell untouched / empty as specified).
    output_destination: Mapped[str | None] = mapped_column(String(512), nullable=True)
    output_remarks: Mapped[str | None] = mapped_column(String(512), nullable=True)
    write_destination: Mapped[bool] = mapped_column(Boolean, default=False)
    write_remarks: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    manual_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    matching_engine_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    job: Mapped[ProcessingJob] = relationship(back_populates="rows")


class PlatformAccount(Base):
    """Normalised public profile snapshot of an account on one platform."""

    __tablename__ = "platform_accounts"
    __table_args__ = (UniqueConstraint("platform", "username_key", name="uq_account_platform_username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16))
    platform_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    username: Mapped[str] = mapped_column(String(64))
    username_key: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    profile_image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stream_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "helix", "kick-public-v1"
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    social_links: Mapped[list[SocialLink]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class SocialLink(Base):
    __tablename__ = "social_links"
    __table_args__ = (Index("ix_social_kind_identity", "kind", "identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("platform_accounts.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    identity: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    unique_identity: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[PlatformAccount] = relationship(back_populates="social_links")


class ImageHashRecord(Base):
    """Perceptual hashes per image URL (profile_images). Also used to detect generic avatars."""

    __tablename__ = "profile_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True)
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True)
    username_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    features_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    low_complexity: Mapped[bool] = mapped_column(Boolean, default=False)
    fetch_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MatchCandidate(Base):
    __tablename__ = "match_candidates"
    __table_args__ = (Index("ix_candidates_source", "source_platform", "source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_platform: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(String(64))
    target_platform: Mapped[str] = mapped_column(String(16))
    target_key: Mapped[str] = mapped_column(String(64))
    discovered_via: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    matching_engine_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MatchDecision(Base):
    __tablename__ = "match_decisions"
    __table_args__ = (Index("ix_decisions_source", "source_platform", "source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_platform: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(String(64))
    source_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_platform: Mapped[str] = mapped_column(String(16))
    target_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    matching_engine_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManualReview(Base):
    """Human verdict on a (source, target) pair. Used as an explicit override, never to retune."""

    __tablename__ = "manual_reviews"
    __table_args__ = (
        UniqueConstraint(
            "source_platform", "source_key", "target_platform", "target_key", name="uq_manual_review_pair"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_platform: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(String(64))
    target_platform: Mapped[str] = mapped_column(String(16))
    target_key: Mapped[str] = mapped_column(String(64))
    target_display: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verdict: Mapped[str] = mapped_column(String(16))  # CONFIRMED | REJECTED
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    matching_engine_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ApiCache(Base):
    __tablename__ = "api_cache"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cache_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cache_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
