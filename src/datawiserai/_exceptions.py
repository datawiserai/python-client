from __future__ import annotations

import re
from typing import Optional


_MAX_MESSAGE_LENGTH = 512
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|x-api-key|cookie|set-cookie|"
    r"location|x-internal-[a-z0-9_-]*|x-vercel-[a-z0-9_-]*)"
    r"\s*[:=]\s*[^\s,;]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_KEY_LIKE_TOKEN = re.compile(
    r"(?i)\b(?:pk|sk|dw|datawiser)_(?:live|test)_[a-z0-9_-]+"
)
_SECRET_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:api[-_]?key|access[-_]?token|token|secret|signature)=)"
    r"[^&#\s]+"
)


def _safe_message(message: object, *, fallback: str = "Request failed") -> str:
    """Return a bounded, single-line message with common secrets redacted."""

    value = str(message).replace("\r", " ").replace("\n", " ").strip()
    if not value:
        value = fallback
    value = _SENSITIVE_VALUE.sub(lambda match: f"{match.group(1)}: [REDACTED]", value)
    value = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
    value = _KEY_LIKE_TOKEN.sub("[REDACTED]", value)
    value = _SECRET_QUERY_VALUE.sub(r"\1[REDACTED]", value)
    if len(value) > _MAX_MESSAGE_LENGTH:
        value = value[: _MAX_MESSAGE_LENGTH - 1].rstrip() + "\N{HORIZONTAL ELLIPSIS}"
    return value


class DatawiserError(Exception):
    """Base exception for all datawiserai errors."""


class DatawiserAPIError(DatawiserError):
    """Base class for failures while communicating with the Datawiser API.

    ``status_code`` and ``message`` remain available for compatibility with
    earlier releases.  A network or protocol failure which has no HTTP status
    uses ``None``.
    """

    def __init__(
        self,
        status_code: Optional[int],
        message: str,
        *,
        possibly_charged: bool = False,
    ) -> None:
        self.status_code = status_code
        self.message = _safe_message(message)
        # Both names are intentionally retained: the shorter name is pleasant
        # for callers and the longer one is unambiguous in diagnostics.
        self.possibly_charged = bool(possibly_charged)
        self.request_may_have_been_charged = self.possibly_charged
        self.retryable = False
        if status_code is None:
            rendered = f"API request error: {self.message}"
        else:
            rendered = f"API error {status_code}: {self.message}"
        super().__init__(rendered)


class DatawiserAuthenticationError(DatawiserAPIError):
    """The API key is missing or was not accepted (HTTP 401)."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(401, message)


class DatawiserAccessError(DatawiserAPIError):
    """The authenticated caller may not access the representation (HTTP 403)."""

    def __init__(self, message: str = "Access denied") -> None:
        super().__init__(403, message)


class DatawiserNotFoundError(DatawiserAPIError):
    """The requested identity or data is unavailable (HTTP 404)."""

    def __init__(self, message: str = "Requested identity or data is unavailable") -> None:
        super().__init__(404, message)


class DatawiserRateLimitError(DatawiserAPIError):
    """The request was rejected by quota or rate limiting (HTTP 429)."""

    def __init__(
        self,
        message: str = "Request quota or rate limit exceeded",
        *,
        retry_after: Optional[float] = None,
        rate_limit_reset: Optional[float] = None,
    ) -> None:
        self.retry_after = retry_after
        self.rate_limit_reset = rate_limit_reset
        self.reset_at = rate_limit_reset
        super().__init__(429, message)
        self.retryable = True


class DatawiserServiceError(DatawiserAPIError):
    """The API returned a server-side failure (HTTP 5xx)."""

    def __init__(
        self,
        status_code: int,
        message: str = "Datawiser service is temporarily unavailable",
        *,
        possibly_charged: bool = False,
    ) -> None:
        super().__init__(status_code, message, possibly_charged=possibly_charged)
        self.retryable = True


class DatawiserNetworkError(DatawiserAPIError):
    """A request failed before a complete HTTP response was received."""

    def __init__(
        self,
        message: str = "Network request failed",
        *,
        possibly_charged: bool = True,
    ) -> None:
        super().__init__(None, message, possibly_charged=possibly_charged)
        self.retryable = True


class DatawiserResponseError(DatawiserAPIError):
    """The API response could not be used safely by this client."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        possibly_charged: bool = False,
    ) -> None:
        super().__init__(
            status_code,
            message,
            possibly_charged=possibly_charged,
        )


class DatawiserCacheError(DatawiserError):
    """A cached representation cannot be read or used safely."""

    def __init__(self, message: str) -> None:
        self.message = _safe_message(message, fallback="Cache data is unusable")
        super().__init__(self.message)


class ManifestCompatibilityError(DatawiserError):
    """A public manifest has a shape this client does not understand."""

    def __init__(self, endpoint: str, detail: str) -> None:
        self.endpoint = endpoint
        self.detail = _safe_message(detail, fallback="unsupported manifest format")
        safe_endpoint = _safe_message(endpoint, fallback="unknown")
        self.message = f"Unsupported manifest for '{safe_endpoint}': {self.detail}"
        super().__init__(self.message)


class TickerNotFoundError(DatawiserError):
    """Raised when a ticker is not present in the endpoint manifest."""

    def __init__(self, ticker: str, endpoint: str) -> None:
        self.ticker = ticker
        self.endpoint = endpoint
        safe_ticker = _safe_message(ticker, fallback="unknown")
        safe_endpoint = _safe_message(endpoint, fallback="unknown")
        super().__init__(
            f"Ticker '{safe_ticker}' not found in the '{safe_endpoint}' manifest"
        )


class AmbiguousTickerError(DatawiserError):
    """Raised when a ticker cannot resolve to one active manifest entry."""

    def __init__(self, ticker: str, endpoint: str, matches: list[str]) -> None:
        self.ticker = ticker
        self.endpoint = endpoint
        self.matches = matches
        safe_ticker = _safe_message(ticker, fallback="unknown")
        safe_endpoint = _safe_message(endpoint, fallback="unknown")
        match_list = _safe_message(
            ", ".join(str(match) for match in matches),
            fallback="none",
        )
        super().__init__(
            f"Ticker '{safe_ticker}' does not resolve to exactly one active security "
            f"in the '{safe_endpoint}' manifest. Matching manifest keys: "
            f"{match_list}. Use a security_id instead."
        )
