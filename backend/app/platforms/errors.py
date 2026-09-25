"""Error taxonomy for platform access.

"Not found" is *not* an exception: adapters return ``None`` for accounts that a
successful lookup did not return. Everything here means "we could not find out".
"""

from __future__ import annotations


class PlatformError(Exception):
    """Base class: the platform could not be queried reliably."""

    row_status = "API_ERROR"
    retryable = True

    def __init__(self, message: str, *, platform: str = "", status_code: int | None = None) -> None:
        super().__init__(message)
        self.platform = platform
        self.status_code = status_code


class ConfigurationError(PlatformError):
    """Missing/invalid credentials. Fatal for the job; user must fix configuration."""

    row_status = "API_ERROR"
    retryable = False


class AuthenticationError(ConfigurationError):
    """401/403 from the platform after a token refresh."""


class RateLimitedError(PlatformError):
    row_status = "RATE_LIMITED"

    def __init__(self, message: str, *, platform: str = "", retry_after: float | None = None) -> None:
        super().__init__(message, platform=platform, status_code=429)
        self.retry_after = retry_after


class TemporaryPlatformError(PlatformError):
    """5xx, timeouts, connection/DNS errors."""

    row_status = "TEMPORARY_ERROR"


class BadRequestError(PlatformError):
    """400/409/422 — permanent for this request; not retried."""

    row_status = "API_ERROR"
    retryable = True  # the *row* can be retried later (e.g. after an adapter fix)


class UnexpectedResponseError(PlatformError):
    row_status = "API_ERROR"
