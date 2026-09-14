from __future__ import annotations

import ipaddress
import math
import re
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from numbers import Real
from typing import Any, Mapping, Optional, Tuple, Union
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

from ._exceptions import (
    DatawiserAPIError,
    DatawiserAccessError,
    DatawiserAuthenticationError,
    DatawiserNetworkError,
    DatawiserNotFoundError,
    DatawiserRateLimitError,
    DatawiserResponseError,
    DatawiserServiceError,
)

BASE_URL = "https://api.datawiser.ai/v1"
DEFAULT_TIMEOUT = 30.0
MAX_ETAG_LENGTH = 1024
MAX_RATE_LIMIT_DELAY = 24 * 60 * 60

Timeout = Union[float, Tuple[float, float]]

# RFC 9110 section 8.8.3: an entity tag is an optional, case-sensitive W/
# prefix followed by a quoted opaque value.  obs-text is represented here as
# Latin-1 code points because Python header values are strings.
_ETAG_RE = re.compile(r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"\Z')


@dataclass(frozen=True)
class TransportResponse:
    """A validated JSON response or an HTTP 304 cache signal."""

    status_code: int
    data: Optional[dict[str, Any]]
    etag: Optional[str] = None

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304


def is_valid_etag(value: object) -> bool:
    """Return whether *value* is one safe, opaque HTTP entity tag.

    Lists and ``*`` are valid in an ``If-None-Match`` field but are not valid
    server ``ETag`` field values, so this deliberately accepts neither.  That
    keeps corrupt cache metadata from becoming an arbitrary request header.
    """

    return (
        isinstance(value, str)
        and len(value) <= MAX_ETAG_LENGTH
        and _ETAG_RE.fullmatch(value) is not None
    )


def canonicalize_base_url(base_url: str) -> str:
    """Validate and canonicalize an API base URL without losing its base path.

    HTTPS is required except for literal localhost and loopback addresses,
    which may use HTTP for offline development and contract tests.
    """

    if not isinstance(base_url, str) or not base_url:
        raise ValueError("base_url must be a non-empty absolute URL")
    if base_url != base_url.strip() or any(
        char.isspace() or ord(char) == 127 for char in base_url
    ):
        raise ValueError("base_url must not contain whitespace or control characters")
    if "\\" in base_url:
        raise ValueError("base_url must not contain backslashes")

    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base_url is not a valid absolute URL") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc or hostname is None:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError("base_url must not contain user information")
    if "?" in base_url or "#" in base_url:
        raise ValueError("base_url must not contain a query string or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("base_url contains an invalid port")

    normalized_host = hostname.rstrip(".").lower()
    if not normalized_host:
        raise ValueError("base_url must include a host")

    loopback = normalized_host == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        pass
    if scheme != "https" and not loopback:
        raise ValueError("base_url must use HTTPS except for localhost or loopback")

    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        try:
            encoded_host = normalized_host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("base_url contains an invalid host") from exc
        if "%" in encoded_host or not encoded_host:
            raise ValueError("base_url contains an invalid host")
        rendered_host = encoded_host
    else:
        rendered_host = f"[{address.compressed}]" if address.version == 6 else str(address)

    default_port = (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    netloc = rendered_host if port is None or default_port else f"{rendered_host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def _validate_timeout(timeout: object) -> Timeout:
    def finite_positive(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("timeout must contain finite positive numbers")
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError("timeout must contain finite positive numbers")
        return number

    if isinstance(timeout, tuple) and len(timeout) == 2:
        return (finite_positive(timeout[0]), finite_positive(timeout[1]))
    return finite_positive(timeout)


def _bounded_retry_after(value: Optional[str], *, now: float) -> Optional[float]:
    if value is None or len(value) > 128:
        return None
    stripped = value.strip()
    try:
        seconds = float(stripped)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(stripped)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = parsed.timestamp() - now
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, float(MAX_RATE_LIMIT_DELAY))


def _bounded_rate_limit_reset(
    headers: Mapping[str, str], *, now: float
) -> Optional[float]:
    value = headers.get("RateLimit-Reset") or headers.get("X-RateLimit-Reset")
    if value is None or len(value) > 128:
        return None
    try:
        number = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(number) or number < 0:
        return None

    # RateLimit-Reset commonly carries delay seconds, while the legacy X-
    # field commonly carries an epoch timestamp.  Normalize either form to a
    # bounded epoch timestamp for callers.
    if number < 1_000_000_000:
        number = now + min(number, float(MAX_RATE_LIMIT_DELAY))
    return min(max(number, now), now + float(MAX_RATE_LIMIT_DELAY))


class Transport:
    """Fetch data from the Datawiser API using one reusable session."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        if (
            not isinstance(api_key, str)
            or not api_key
            or api_key != api_key.strip()
            or any(not 33 <= ord(char) <= 126 for char in api_key)
        ):
            raise ValueError("api_key must be a non-empty single-line string")

        self.base_url = canonicalize_base_url(base_url)
        self.timeout = _validate_timeout(timeout)
        self._session = requests.Session()
        # Environment authentication (notably netrc) could add an ordinary
        # Authorization field alongside X-API-Key. This API intentionally uses
        # only its own credential header, so do not inherit that configuration.
        self._session.trust_env = False
        # Keep application fields deliberately small while explicitly
        # retaining standard HTTP compression negotiation.
        self._session.headers.clear()
        self._session.headers.update(
            {
                "X-API-Key": api_key,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        # Requests already defaults to zero retries. Explicit adapters make the
        # metered-request guarantee independent of library default changes.
        self._session.mount("https://", HTTPAdapter(max_retries=0))
        self._session.mount("http://", HTTPAdapter(max_retries=0))

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    @staticmethod
    def _segment(value: str, *, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        return quote(value, safe="")

    def _request_json(
        self,
        url: str,
        *,
        etag: Optional[str] = None,
        possibly_charged: bool,
    ) -> TransportResponse:
        headers: dict[str, str] = {}
        if is_valid_etag(etag):
            headers["If-None-Match"] = etag  # type: ignore[assignment]

        # Do not retain or replay cookies even if an unexpected response tries
        # to set one. The data API authenticates exclusively with X-API-Key.
        self._session.cookies.clear()

        try:
            response = self._session.get(
                url,
                headers=headers or None,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            # Exception strings can contain prepared headers or proxy URLs.
            # Suppress that context rather than exposing it in a traceback.
            raise DatawiserNetworkError(
                "Network request failed before a complete response was received",
                possibly_charged=possibly_charged,
            ) from None

        status_code = response.status_code
        if status_code == 304:
            if response.content:
                raise DatawiserResponseError(
                    "Datawiser API returned an invalid 304 response body",
                    status_code=status_code,
                    possibly_charged=False,
                )
            return TransportResponse(
                status_code=status_code,
                data=None,
                etag=self._response_etag(response, possibly_charged=False),
            )

        if status_code == 200:
            response_etag = self._response_etag(
                response,
                possibly_charged=possibly_charged,
            )
            try:
                data = response.json()
            except (requests.exceptions.JSONDecodeError, ValueError):
                raise DatawiserResponseError(
                    "Datawiser API returned malformed JSON",
                    status_code=status_code,
                    possibly_charged=possibly_charged,
                ) from None
            if not isinstance(data, dict):
                raise DatawiserResponseError(
                    "Datawiser API returned a JSON value with an unsupported shape",
                    status_code=status_code,
                    possibly_charged=possibly_charged,
                )
            return TransportResponse(status_code, data, response_etag)

        self._raise_for_status(
            status_code,
            response.headers,
            possibly_charged=possibly_charged,
        )
        raise AssertionError("unreachable")

    @staticmethod
    def _response_etag(
        response: requests.Response, *, possibly_charged: bool
    ) -> Optional[str]:
        etag = response.headers.get("ETag")
        if etag is None:
            return None
        if not is_valid_etag(etag):
            raise DatawiserResponseError(
                "Datawiser API returned a malformed ETag",
                status_code=response.status_code,
                possibly_charged=possibly_charged,
            )
        return etag

    @staticmethod
    def _raise_for_status(
        status_code: int,
        headers: Mapping[str, str],
        *,
        possibly_charged: bool,
    ) -> None:
        # Deliberately do not render the response body, headers, URL, or a
        # redirect Location. Public status messages cannot echo server secrets
        # or internal diagnostics.
        if status_code == 401:
            raise DatawiserAuthenticationError()
        if status_code == 403:
            raise DatawiserAccessError()
        if status_code == 404:
            raise DatawiserNotFoundError()
        if status_code == 429:
            now = time.time()
            raise DatawiserRateLimitError(
                retry_after=_bounded_retry_after(headers.get("Retry-After"), now=now),
                rate_limit_reset=_bounded_rate_limit_reset(headers, now=now),
            )
        if 500 <= status_code <= 599:
            raise DatawiserServiceError(
                status_code,
                possibly_charged=possibly_charged,
            )
        if 300 <= status_code <= 399:
            raise DatawiserAPIError(status_code, "Unexpected redirect response")
        raise DatawiserAPIError(status_code, "Datawiser API request failed")

    def get_resource(
        self,
        endpoint: str,
        identifier: str,
        etag: Optional[str] = None,
    ) -> TransportResponse:
        """GET one original identifier, optionally with a safe entity tag."""

        endpoint_segment = self._segment(endpoint, name="endpoint")
        identifier_segment = self._segment(identifier, name="identifier")
        return self._request_json(
            f"{self.base_url}/{endpoint_segment}/{identifier_segment}",
            etag=etag,
            possibly_charged=True,
        )

    def get_manifest_response(
        self,
        endpoint: str,
        *,
        updates: bool = False,
        etag: Optional[str] = None,
    ) -> TransportResponse:
        """GET public manifest metadata, optionally conditionally."""

        endpoint_segment = self._segment(endpoint, name="endpoint")
        suffix = "/updates" if updates else ""
        return self._request_json(
            f"{self.base_url}/manifest/{endpoint_segment}{suffix}",
            etag=etag,
            possibly_charged=False,
        )

    def get_manifest(self, endpoint: str) -> dict[str, Any]:
        """Return the legacy updates manifest representation."""

        response = self.get_manifest_response(endpoint, updates=True)
        if response.data is None:
            raise DatawiserResponseError(
                "Datawiser API returned an unsolicited 304 for a manifest",
                status_code=response.status_code,
            )
        return response.data

    def get(self, endpoint: str, ticker: str) -> dict[str, Any]:
        """Return the legacy unconditional JSON representation for one item."""

        response = self.get_resource(endpoint, ticker)
        if response.data is None:
            raise DatawiserResponseError(
                "Datawiser API returned an unsolicited 304 for a data request",
                status_code=response.status_code,
            )
        return response.data
