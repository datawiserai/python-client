from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Union

from ._cache import CacheCandidate, FileCache
from ._exceptions import (
    AmbiguousTickerError,
    DatawiserCacheError,
    DatawiserResponseError,
    ManifestCompatibilityError,
    TickerNotFoundError,
)
from ._transport import (
    BASE_URL,
    Timeout,
    Transport,
    TransportResponse,
    is_valid_etag,
)
from .models.free_float import FreeFloat
from .models.free_float_events import FreeFloatEvents, FreeFloatEventsDetail
from .models.reference import Reference
from .models.shares_outstanding import SharesOutstanding
from .models.universe import Universe

logger = logging.getLogger("datawiserai")

_DEFAULT_CACHE_DIR = Path.home() / ".datawiserai" / "cache"
_DEFAULT_MANIFEST_TTL = 300.0
_DEFAULT_CACHE_MAX_BYTES = 256 * 1024 * 1024
_DEFAULT_CACHE_MAX_ENTRIES = 2_000
_CONDITIONAL_ENDPOINTS = frozenset(("shares-outstanding", "reference"))

ENDPOINTS = (
    "free-float",
    "free-float-events",
    "shares-outstanding",
    "reference",
)

_ALIAS = {ep.replace("-", "_"): ep for ep in ENDPOINTS}


def _resolve_endpoint(name: str) -> str:
    """Accept both ``'free-float'`` and ``'free_float'``."""
    return _ALIAS.get(name, name)


def _checked_endpoint(name: str) -> str:
    endpoint = _resolve_endpoint(name)
    if endpoint not in ENDPOINTS:
        allowed = ", ".join(ENDPOINTS)
        raise ValueError(f"Unknown endpoint {name!r}; expected one of: {allowed}")
    return endpoint


def _weak_etag_equal(left: str, right: str) -> bool:
    """Compare two already-validated entity tags using HTTP weak comparison."""
    left_opaque = left[2:] if left.startswith("W/") else left
    right_opaque = right[2:] if right.startswith("W/") else right
    return left_opaque == right_opaque


def _manifest_entry_identity(key: str, entry: dict[str, Any]) -> tuple[str, str]:
    export_file = entry.get("export_file")
    if export_file:
        return ("export_file", export_file)

    security_id = entry.get("security_id")
    if security_id:
        return ("security_id", security_id)

    return ("manifest_key", key)


def _is_full_export_entry(key: str, entry: dict[str, Any]) -> bool:
    export_file = entry.get("export_file", "")
    export_stem = entry.get("export_stem", "")
    return (
        export_file.endswith("-full.json.gz")
        or export_file.endswith("-full.json")
        or export_stem.endswith("-full")
        or key.endswith("-full")
    )


def _manifest_fetch_key(
    manifest: dict[str, Any], key: str, entry: dict[str, Any]
) -> str:
    export_stem = entry.get("export_stem")
    if export_stem and export_stem in manifest:
        return export_stem
    return key


class Client:
    """Entry-point for the Datawiser Python SDK.

    Parameters
    ----------
    api_key : str
        Your Datawiser API key.
    cache_dir : str or Path, optional
        Where to store cached responses.  Defaults to
        ``~/.datawiserai/cache``.
    use_cache : bool
        Set to *False* to bypass cache reads and writes. If
        ``refresh_cache=True`` is also set, fresh responses are still written.
    refresh_cache : bool
        Set to *True* to fetch fresh API responses and overwrite cached files.
        This skips cache reads even when a matching cached response exists.
    base_url : str
        API prefix, including its base path. Defaults to
        ``https://api.datawiser.ai/v1``.
    timeout : float or tuple
        Finite Requests timeout (seconds), or ``(connect, read)`` timeout.
    manifest_ttl : float
        Maximum age in seconds for cached discovery metadata.
    """

    def __init__(
        self,
        api_key: str,
        *,
        cache_dir: Union[str, Path, None] = None,
        use_cache: bool = True,
        refresh_cache: bool = False,
        base_url: str = BASE_URL,
        timeout: Timeout = 30.0,
        manifest_ttl: float = _DEFAULT_MANIFEST_TTL,
        cache_max_bytes: int = _DEFAULT_CACHE_MAX_BYTES,
        cache_max_entries: int = _DEFAULT_CACHE_MAX_ENTRIES,
    ) -> None:
        if isinstance(manifest_ttl, bool) or not isinstance(
            manifest_ttl, (int, float)
        ):
            raise TypeError("manifest_ttl must be a finite non-negative number")
        if not math.isfinite(float(manifest_ttl)) or manifest_ttl < 0:
            raise ValueError("manifest_ttl must be a finite non-negative number")

        self._transport = Transport(api_key, base_url=base_url, timeout=timeout)
        self._use_cache = use_cache
        self._refresh_cache = refresh_cache
        self._manifest_ttl = float(manifest_ttl)
        try:
            self._cache = FileCache(
                Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR,
                api_base=self._transport.base_url,
                api_key=api_key,
                max_bytes=cache_max_bytes,
                max_entries=cache_max_entries,
            )
        except OSError as exc:
            self._transport.close()
            raise DatawiserCacheError(
                "The configured cache directory cannot be used safely."
            ) from exc
        self._diagnostics = {
            "data_requests": 0,
            "conditional_requests": 0,
            "not_modified_responses": 0,
            "cached_304_returns": 0,
            "unconditional_recoveries": 0,
            "responses_without_etag": 0,
            "manifest_requests": 0,
            "cache_write_failures": 0,
        }

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _get_manifest(self, endpoint: str, *, refresh: bool = False) -> dict:
        endpoint = _checked_endpoint(endpoint)
        can_read = self._use_cache and not self._refresh_cache and not refresh
        if can_read:
            try:
                cached = self._cache.get_manifest(
                    endpoint, max_age_seconds=self._manifest_ttl
                )
            except OSError as exc:
                raise DatawiserCacheError(
                    "Cached manifest metadata cannot be used safely."
                ) from exc
            if cached is not None:
                return cached

        self._diagnostics["manifest_requests"] += 1
        if hasattr(self._transport, "get_manifest_response"):
            response = self._transport.get_manifest_response(
                endpoint, updates=True
            )
            if response.status_code != 200 or not isinstance(response.data, dict):
                raise DatawiserResponseError(
                    "The manifest updates endpoint returned an unusable response."
                )
            manifest = response.data
        else:
            # Compatibility seam for existing local test transports.
            manifest = self._transport.get_manifest(endpoint)
            if not isinstance(manifest, dict):
                raise DatawiserResponseError(
                    "The manifest updates endpoint returned an unusable response."
                )

        if any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in manifest.items()
        ):
            raise ManifestCompatibilityError(
                endpoint,
                "expected a dictionary of public update-entry objects",
            )

        if self._use_cache or self._refresh_cache:
            try:
                self._cache.put_manifest(endpoint, manifest)
            except OSError:
                self._diagnostics["cache_write_failures"] += 1
                logger.warning("could not persist manifest cache metadata")
        return manifest

    def _resource_response(
        self, endpoint: str, identifier: str, *, etag: Union[str, None] = None
    ) -> TransportResponse:
        self._diagnostics["data_requests"] += 1
        if etag is not None:
            self._diagnostics["conditional_requests"] += 1
        if hasattr(self._transport, "get_resource"):
            return self._transport.get_resource(
                endpoint, identifier, etag=etag
            )

        # Compatibility seam for the repository's pre-D4 fake transports.
        data = self._transport.get(endpoint, identifier)
        return TransportResponse(status_code=200, data=data, etag=None)

    @staticmethod
    def _validate_payload(endpoint: str, data: Any) -> str:
        if not isinstance(data, dict):
            raise DatawiserResponseError(
                "The API returned a non-object JSON representation.",
                possibly_charged=True,
            )

        try:
            if endpoint == "shares-outstanding":
                model = SharesOutstanding._from_dict(data)
            elif endpoint == "reference":
                model = Reference._from_dict(data)
            elif endpoint == "free-float":
                model = FreeFloat._from_dict(data)
            elif endpoint == "free-float-events":
                model = FreeFloatEvents._from_dict(data)
                # Both established views must remain constructible from a
                # representation before that representation enters the cache.
                FreeFloatEventsDetail._from_dict(data)
            else:  # pragma: no cover - guarded by _checked_endpoint
                raise ValueError("unsupported dataset")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise DatawiserResponseError(
                f"The API returned an invalid {endpoint} representation.",
                possibly_charged=True,
            ) from exc

        security_id = model.security_id
        if not isinstance(security_id, str) or not security_id:
            raise DatawiserResponseError(
                f"The API returned {endpoint} data without a securityId.",
                possibly_charged=True,
            )
        return security_id

    def _cache_response(
        self,
        endpoint: str,
        identifier: str,
        response: TransportResponse,
        *,
        source_last_update: Union[str, None] = None,
    ) -> dict[str, Any]:
        data = response.data
        security_id = self._validate_payload(endpoint, data)
        etag = response.etag if is_valid_etag(response.etag) else None
        if endpoint in _CONDITIONAL_ENDPOINTS and etag is None:
            self._diagnostics["responses_without_etag"] += 1
        if self._use_cache or self._refresh_cache:
            try:
                self._cache.put_data(
                    endpoint,
                    identifier,
                    security_id,
                    data,
                    etag=etag,
                    source_last_update=source_last_update,
                )
            except OSError:
                self._diagnostics["cache_write_failures"] += 1
                logger.warning("could not persist a validated data response")
        return data

    def _unconditional_recovery(
        self, endpoint: str, identifier: str
    ) -> dict[str, Any]:
        self._diagnostics["unconditional_recoveries"] += 1
        recovered = self._resource_response(endpoint, identifier)
        if recovered.status_code == 304:
            raise DatawiserCacheError(
                "An unconditional cache recovery also returned 304; no "
                "representation body is available."
            )
        if recovered.status_code != 200:
            raise DatawiserResponseError(
                "Cache recovery returned an unexpected response status.",
                possibly_charged=False,
            )
        return self._cache_response(endpoint, identifier, recovered)

    def _fetch_conditional(self, endpoint: str, identifier: str) -> dict[str, Any]:
        candidate: Union[CacheCandidate, None] = None
        if self._use_cache and not self._refresh_cache:
            try:
                candidate = self._cache.get_data(endpoint, identifier)
            except OSError as exc:
                raise DatawiserCacheError(
                    "Cached response metadata cannot be used safely."
                ) from exc

        request_etag = None
        if candidate is not None and is_valid_etag(candidate.etag):
            request_etag = candidate.etag

        response = self._resource_response(
            endpoint, identifier, etag=request_etag
        )
        if response.status_code == 200:
            return self._cache_response(endpoint, identifier, response)
        if response.status_code != 304:
            raise DatawiserResponseError(
                "The data endpoint returned an unexpected response status.",
                possibly_charged=False,
            )

        self._diagnostics["not_modified_responses"] += 1
        usable = candidate is not None and request_etag is not None
        if usable and response.etag is not None:
            usable = is_valid_etag(response.etag) and _weak_etag_equal(
                request_etag, response.etag
            )

        if usable:
            try:
                cached_data = self._cache.reload_data(candidate)
            except OSError:
                cached_data = None
            if cached_data is not None:
                try:
                    security_id = self._validate_payload(endpoint, cached_data)
                except DatawiserResponseError:
                    cached_data = None
                else:
                    if security_id != candidate.security_id:
                        cached_data = None
            if cached_data is not None:
                self._diagnostics["cached_304_returns"] += 1
                if response.etag is not None and response.etag != candidate.etag:
                    try:
                        self._cache.put_data(
                            endpoint,
                            identifier,
                            candidate.security_id,
                            cached_data,
                            etag=response.etag,
                            source_last_update=candidate.source_last_update,
                        )
                    except OSError:
                        self._diagnostics["cache_write_failures"] += 1
                        logger.warning(
                            "could not persist refreshed cache metadata"
                        )
                return cached_data

        # This is the only retry-like operation in the client. It is bounded
        # to one unconditional recovery after an unusable 304.
        return self._unconditional_recovery(endpoint, identifier)

    def _resolve_manifest_entry(
        self, endpoint: str, manifest: dict[str, Any], ticker: str
    ) -> tuple[str, dict[str, Any]]:
        for value in manifest.values():
            for field in (
                "ticker",
                "security_id",
                "export_file",
                "export_stem",
                "last_update",
                "doc_last_update",
            ):
                if field in value and value[field] is not None and not isinstance(
                    value[field], str
                ):
                    raise ManifestCompatibilityError(
                        endpoint,
                        f"an update entry has a non-string {field}",
                    )
            if "is_delisted" in value and not isinstance(
                value["is_delisted"], bool
            ):
                raise ManifestCompatibilityError(
                    endpoint,
                    "an update entry has a non-boolean is_delisted",
                )

        entry = manifest.get(ticker)
        if entry is not None:
            return _manifest_fetch_key(manifest, ticker, entry), entry

        matches = [
            (key, val)
            for key, val in manifest.items()
            if isinstance(val, dict) and val.get("ticker") == ticker
        ]
        if not matches:
            raise TickerNotFoundError(ticker, endpoint)

        active_matches = [
            (key, val)
            for key, val in matches
            if not val.get("is_delisted", False)
            and not _is_full_export_entry(key, val)
        ]
        unique_active: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        for key, val in active_matches:
            unique_active.setdefault(_manifest_entry_identity(key, val), (key, val))

        if len(unique_active) == 1:
            key, entry = next(iter(unique_active.values()))
            return _manifest_fetch_key(manifest, key, entry), entry

        raise AmbiguousTickerError(
            ticker,
            endpoint,
            [key for key, _val in active_matches or matches],
        )

    def _fetch(self, endpoint: str, ticker: str) -> dict:
        """Return parsed JSON for *ticker* using the dataset's safe flow."""
        endpoint = _checked_endpoint(endpoint)
        if not isinstance(ticker, str) or not ticker:
            raise ValueError("ticker must be a non-empty string")

        if endpoint in _CONDITIONAL_ENDPOINTS:
            # The original identifier is deliberately passed through. A cache
            # alias is evidence of a past response, never current ownership.
            return self._fetch_conditional(endpoint, ticker)

        # FF/FFE retain their established public manifest resolution because
        # those hosted routes have not established conditional-read support.
        # The data route is nevertheless called every time: cached metadata is
        # not treated as proof of current access.
        manifest = self._get_manifest(endpoint)
        resolved_ticker, entry = self._resolve_manifest_entry(
            endpoint, manifest, ticker
        )
        remote_ts = entry.get("last_update")
        if not isinstance(remote_ts, str):
            raise ManifestCompatibilityError(
                endpoint, "the selected update entry is missing last_update"
            )

        response = self._resource_response(endpoint, resolved_ticker)
        if response.status_code == 304:
            # No validator was sent for these datasets. Recover once rather
            # than ever presenting an empty success.
            return self._unconditional_recovery(endpoint, resolved_ticker)
        if response.status_code != 200:
            raise DatawiserResponseError(
                "The data endpoint returned an unexpected response status.",
                possibly_charged=False,
            )
        return self._cache_response(
            endpoint,
            ticker,
            response,
            source_last_update=remote_ts,
        )

    # ------------------------------------------------------------------
    # universe
    # ------------------------------------------------------------------

    def universe(self, endpoint: str, *, refresh: bool = False) -> Universe:
        """Return the set of available tickers for *endpoint*.

        Parameters
        ----------
        endpoint : str
            One of ``'free-float'``, ``'free-float-events'``,
            ``'shares-outstanding'``, or ``'reference'``.
            Underscore variants (e.g. ``'free_float'``) are also accepted.
        refresh : bool
            If *True*, bypass cached discovery metadata for this call.

        Returns
        -------
        Universe
            Object with ``.tickers``, ``.to_dataframe()``, and ``in``
            membership testing.
        """
        endpoint = _checked_endpoint(endpoint)
        manifest = self._get_manifest(endpoint, refresh=refresh)
        return Universe._from_manifest(endpoint, manifest)

    # ------------------------------------------------------------------
    # endpoint methods
    # ------------------------------------------------------------------

    def free_float(self, ticker: str) -> FreeFloat:
        """Fetch free-float data for *ticker*.

        Returns a :class:`~datawiserai.models.FreeFloat` object whose
        ``.events`` attribute contains the full time-series.  Call
        ``.to_dataframe()`` for a pandas DataFrame.
        """
        data = self._fetch("free-float", ticker)
        return FreeFloat._from_dict(data)

    def free_float_events(self, ticker: str) -> FreeFloatEvents:
        """Fetch free-float events (flat/summary view) for *ticker*.

        Each owner-date pair is flattened into a
        :class:`~datawiserai.models.FreeFloatOwnerSummary`.  Nested
        structures (sub-components, restrictions, options, event details)
        are omitted.  Call ``.to_dataframe()`` for a pandas DataFrame.
        """
        data = self._fetch("free-float-events", ticker)
        return FreeFloatEvents._from_dict(data)

    def free_float_events_detail(self, ticker: str) -> FreeFloatEventsDetail:
        """Fetch free-float events (full drill-down) for *ticker*.

        Returns the complete nested ownership structure.  Use this for
        interactive exploration rather than tabular analysis::

            detail = client.free_float_events_detail("OLP")
            ev = detail[0]                          # first event date
            ev.owner_names                          # {id: name, ...}
            owner = ev.owner("OLPzC085ekEKV")       # full raw dict
            owner["components"]                     # sub-components
            owner["restrictions"]                   # restrictions
            owner["eventDetails"]                   # event details
        """
        data = self._fetch("free-float-events", ticker)
        return FreeFloatEventsDetail._from_dict(data)

    def shares_outstanding(self, ticker: str) -> SharesOutstanding:
        """Fetch shares-outstanding data for *ticker*.

        Returns a :class:`~datawiserai.models.SharesOutstanding` object.
        Call ``.to_dataframe()`` for a pandas DataFrame.
        """
        data = self._fetch("shares-outstanding", ticker)
        return SharesOutstanding._from_dict(data)

    def reference(self, ticker: str) -> Reference:
        """Fetch reference / identifier data for *ticker*.

        Returns a :class:`~datawiserai.models.Reference` object.
        The full raw JSON is accessible via ``.raw``.
        """
        data = self._fetch("reference", ticker)
        return Reference._from_dict(data)

    # ------------------------------------------------------------------
    # cache management
    # ------------------------------------------------------------------

    def clear_cache(self, endpoint: Union[str, None] = None) -> int:
        """Remove cached data.  Returns the number of files deleted.

        Parameters
        ----------
        endpoint : str, optional
            Limit clearing to a single endpoint (e.g. ``"free-float"``).
            If omitted the entire cache is wiped.
        """
        if endpoint is not None:
            endpoint = _checked_endpoint(endpoint)
        try:
            return self._cache.clear(endpoint)
        except OSError as exc:
            raise DatawiserCacheError(
                "The selected cache namespace cannot be cleared safely."
            ) from exc

    @property
    def diagnostics(self) -> dict[str, int]:
        """Return non-sensitive request/cache counters for this client."""
        return dict(self._diagnostics)

    @property
    def base_url(self) -> str:
        """The canonical API prefix used by this client."""
        return self._transport.base_url

    def close(self) -> None:
        """Close the reusable HTTP session."""
        close = getattr(self._transport, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
