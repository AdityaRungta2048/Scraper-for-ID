"""Persistent TTL cache (api_cache table) with a small in-process front cache.

Stores platform lookups (including *negative* lookups, with a shorter TTL), search
results, and whole source resolutions (keyed by matching-engine version).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.models import ApiCache

_MISSING = object()


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class CacheService:
    def __init__(self, session_factory: sessionmaker[Session] | None, memory_limit: int = 20000) -> None:
        self._sf = session_factory
        self._mem: dict[str, tuple[datetime, Any]] = {}
        self._lock = threading.Lock()
        self._memory_limit = memory_limit
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _full_key(namespace: str, key: str) -> str:
        return f"{namespace}:{key}"

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        full = self._full_key(namespace, key)
        now = datetime.now(UTC)
        with self._lock:
            item = self._mem.get(full)
            if item is not None:
                if item[0] > now:
                    self.hits += 1
                    return item[1]
                self._mem.pop(full, None)
        if self._sf is not None:
            with self._sf() as session:
                row = session.get(ApiCache, full)
                if row is not None and as_utc(row.expires_at) > now:
                    with self._lock:
                        self._mem[full] = (as_utc(row.expires_at), row.payload)
                    self.hits += 1
                    return row.payload
        self.misses += 1
        return default

    def has(self, namespace: str, key: str) -> bool:
        return self.get(namespace, key, _MISSING) is not _MISSING

    def set(
        self,
        namespace: str,
        key: str,
        payload: Any,
        ttl_seconds: int,
        source: str | None = None,
        source_version: str | None = None,
    ) -> None:
        full = self._full_key(namespace, key)
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl_seconds)
        with self._lock:
            if len(self._mem) >= self._memory_limit:
                self._mem.clear()
            self._mem[full] = (expires, payload)
        if self._sf is None:
            return
        with self._sf() as session:
            row = session.get(ApiCache, full)
            if row is None:
                row = ApiCache(
                    key=full,
                    namespace=namespace,
                    payload=payload,
                    source=source,
                    source_version=source_version,
                    cache_created_at=now,
                    cache_updated_at=now,
                    expires_at=expires,
                )
                session.add(row)
            else:
                row.payload = payload
                row.source = source
                row.source_version = source_version
                row.cache_updated_at = now
                row.expires_at = expires
            session.commit()

    def delete(self, namespace: str, key: str) -> None:
        full = self._full_key(namespace, key)
        with self._lock:
            self._mem.pop(full, None)
        if self._sf is not None:
            with self._sf() as session:
                row = session.get(ApiCache, full)
                if row is not None:
                    session.delete(row)
                    session.commit()

    def clear_memory(self) -> None:
        with self._lock:
            self._mem.clear()
