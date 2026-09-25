"""Resilient HTTP access for platform APIs.

* bounded concurrency (semaphore) + token-bucket request rate limit
* exponential backoff with jitter for 429 / 5xx / timeouts / connection errors
* honours ``Retry-After`` and Twitch's ``Ratelimit-Reset``
* one automatic token refresh on 401, then a clear configuration error
* never logs tokens or secrets
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.logging_setup import get_logger
from app.platforms.errors import (
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    PlatformError,
    RateLimitedError,
    TemporaryPlatformError,
    UnexpectedResponseError,
)

log = get_logger("http")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

SleepFn = Callable[[float], Awaitable[None]]


class TokenBucket:
    """Async token bucket: ``rate_per_minute`` sustained, ``burst`` capacity."""

    def __init__(
        self, rate_per_minute: float, burst: int | None = None, clock: Callable[[], float] = time.monotonic
    ):
        self.rate = max(rate_per_minute, 1) / 60.0
        self.capacity = float(burst if burst is not None else max(1, int(rate_per_minute // 6) or 1))
        self.tokens = self.capacity
        self.clock = clock
        self.updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self, sleep: SleepFn = asyncio.sleep) -> None:
        async with self._lock:
            while True:
                now = self.clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await sleep((1 - self.tokens) / self.rate)


@dataclass
class RetryPolicy:
    retries: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0

    def backoff(self, attempt: int) -> float:
        delay = min(self.max_delay, self.base_delay * (2**attempt))
        return delay + random.uniform(0, self.base_delay) if self.base_delay > 0 else 0.0


class ClientCredentialsToken:
    """OAuth2 client-credentials (app access token) provider shared by Twitch and Kick."""

    def __init__(
        self,
        *,
        platform: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient,
        extra_form: dict[str, str] | None = None,
    ) -> None:
        self.platform = platform
        self.token_url = token_url
        self.client_id = client_id
        self._secret = client_secret
        self._client = client
        self._extra = extra_form or {}
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0

    async def get(self) -> str:
        if not self.client_id or not self._secret:
            raise ConfigurationError(
                f"{self.platform} API credentials are not configured "
                f"({self.platform.upper()}_CLIENT_ID / {self.platform.upper()}_CLIENT_SECRET)",
                platform=self.platform,
            )
        async with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token
            form = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self._secret,
                **self._extra,
            }
            try:
                resp = await self._client.post(
                    self.token_url,
                    data=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise TemporaryPlatformError(
                    f"{self.platform} token endpoint unreachable: {type(exc).__name__}",
                    platform=self.platform,
                ) from exc
            if resp.status_code in (400, 401, 403):
                raise AuthenticationError(
                    f"{self.platform} rejected the client credentials (HTTP {resp.status_code}). "
                    "Check the client id/secret.",
                    platform=self.platform,
                    status_code=resp.status_code,
                )
            if resp.status_code == 429:
                raise RateLimitedError(f"{self.platform} token endpoint rate limited", platform=self.platform)
            if resp.status_code >= 500:
                raise TemporaryPlatformError(
                    f"{self.platform} token endpoint error {resp.status_code}",
                    platform=self.platform,
                    status_code=resp.status_code,
                )
            try:
                body = resp.json()
                token = str(body["access_token"])
                expires_in = float(body.get("expires_in") or 3600)
            except Exception as exc:
                raise UnexpectedResponseError(
                    f"{self.platform} token endpoint returned an unexpected body", platform=self.platform
                ) from exc
            self._token = token
            self._expires_at = time.time() + expires_in
            log.info("oauth_token_acquired", platform=self.platform, expires_in=int(expires_in))
            return token


class PlatformHttpClient:
    def __init__(
        self,
        *,
        platform: str,
        base_url: str,
        client: httpx.AsyncClient,
        token: ClientCredentialsToken | None,
        auth_headers: Callable[[str], dict[str, str]] | None = None,
        concurrency: int = 4,
        requests_per_minute: float = 600,
        retry: RetryPolicy | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self.platform = platform
        self.base_url = base_url.rstrip("/")
        self.client = client
        self.token = token
        self.auth_headers = auth_headers or (lambda t: {"Authorization": f"Bearer {t}"})
        self.semaphore = asyncio.Semaphore(max(1, concurrency))
        self.bucket = TokenBucket(requests_per_minute)
        self.retry = retry or RetryPolicy()
        self.sleep = sleep
        self.request_count = 0

    def _retry_after(self, resp: httpx.Response) -> float | None:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return max(0.0, float(ra))
            except ValueError:
                pass
        reset = resp.headers.get("Ratelimit-Reset")
        if reset:
            try:
                return max(0.0, float(reset) - time.time())
            except ValueError:
                pass
        return None

    async def get_json(self, path: str, params: Any = None, *, endpoint_category: str = "") -> Any:
        return await self.request_json("GET", path, params=params, endpoint_category=endpoint_category)

    async def request_json(
        self, method: str, path: str, *, params: Any = None, endpoint_category: str = ""
    ) -> Any:
        if path.startswith("http"):
            url = path
        elif not path:
            url = self.base_url
        else:
            url = f"{self.base_url}/{path.lstrip('/')}"
        refreshed = False
        attempt = 0
        last_error: PlatformError | None = None
        while True:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self.token is not None:
                headers.update(self.auth_headers(await self.token.get()))
            await self.bucket.acquire(self.sleep)
            started = time.monotonic()
            try:
                async with self.semaphore:
                    self.request_count += 1
                    resp = await self.client.request(method, url, params=params, headers=headers)
            except httpx.TimeoutException:
                last_error = TemporaryPlatformError(
                    f"{self.platform} request timed out", platform=self.platform
                )
                log.warning(
                    "api_timeout", platform=self.platform, endpoint=endpoint_category, attempt=attempt
                )
            except httpx.TransportError as exc:  # connection refused, DNS, reset ...
                last_error = TemporaryPlatformError(
                    f"{self.platform} network error: {type(exc).__name__}", platform=self.platform
                )
                log.warning(
                    "api_network_error", platform=self.platform, endpoint=endpoint_category, attempt=attempt
                )
            else:
                status = resp.status_code
                log.debug(
                    "api_response",
                    platform=self.platform,
                    endpoint=endpoint_category,
                    status=status,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                if 200 <= status < 300:
                    if status == 204 or not resp.content:
                        return None
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise UnexpectedResponseError(
                            f"{self.platform} returned non-JSON for {endpoint_category}",
                            platform=self.platform,
                        ) from exc
                if status == 404:
                    return None  # callers interpret an empty result; lookups by id/login use 200+empty
                if status == 401 and self.token is not None and not refreshed:
                    self.token.invalidate()
                    refreshed = True
                    continue
                if status in (401, 403):
                    raise AuthenticationError(
                        f"{self.platform} API denied access (HTTP {status}) for {endpoint_category}. "
                        "Check credentials / app configuration.",
                        platform=self.platform,
                        status_code=status,
                    )
                if status in (400, 409, 422):
                    raise BadRequestError(
                        f"{self.platform} rejected the request (HTTP {status}) for {endpoint_category}",
                        platform=self.platform,
                        status_code=status,
                    )
                if status == 429:
                    wait = self._retry_after(resp)
                    last_error = RateLimitedError(
                        f"{self.platform} rate limit exceeded", platform=self.platform, retry_after=wait
                    )
                    log.warning(
                        "api_rate_limited", platform=self.platform, endpoint=endpoint_category, wait=wait
                    )
                    if attempt < self.retry.retries:
                        delay = wait if wait is not None else self.retry.backoff(attempt)
                        await self.sleep(
                            min(delay, self.retry.max_delay * 2) if self.retry.base_delay > 0 else 0
                        )
                        attempt += 1
                        continue
                    raise last_error
                if status in RETRYABLE_STATUS:
                    last_error = TemporaryPlatformError(
                        f"{self.platform} server error HTTP {status}",
                        platform=self.platform,
                        status_code=status,
                    )
                else:
                    raise UnexpectedResponseError(
                        f"{self.platform} unexpected HTTP {status} for {endpoint_category}",
                        platform=self.platform,
                        status_code=status,
                    )
            # transient failure path
            if attempt >= self.retry.retries:
                assert last_error is not None
                raise last_error
            await self.sleep(self.retry.backoff(attempt))
            attempt += 1


async def download_image(
    client: httpx.AsyncClient,
    url: str,
    *,
    allowed_hosts: set[str],
    max_bytes: int,
    retry: RetryPolicy | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> bytes | None:
    """Fetch a public profile image. Only https + allow-listed CDN hosts (prevents SSRF).

    Returns None when the image is not retrievable (404, disallowed host, too large);
    raises TemporaryPlatformError only for transient failures after retries.
    """
    parsed = httpx.URL(url)
    host = (parsed.host or "").lower()
    if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        return None
    retry = retry or RetryPolicy(retries=2)
    for attempt in range(retry.retries + 1):
        try:
            async with client.stream("GET", url, headers={"Accept": "image/*"}) as resp:
                if resp.status_code in RETRYABLE_STATUS:
                    raise TemporaryPlatformError(f"image host HTTP {resp.status_code}")
                if resp.status_code != 200:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                if ctype and not ctype.startswith("image/") and "octet-stream" not in ctype:
                    return None
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except (httpx.TimeoutException, httpx.TransportError, TemporaryPlatformError) as exc:
            if attempt >= retry.retries:
                raise TemporaryPlatformError(f"image download failed: {type(exc).__name__}") from exc
            await sleep(retry.backoff(attempt))
    return None
