from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

from ._cache import FileCache
from ._exceptions import TickerNotFoundError
from ._transport import Transport
from .models.free_float import FreeFloat
from .models.free_float_events import FreeFloatEvents, FreeFloatEventsDetail
from .models.reference import Reference
from .models.shares_outstanding import SharesOutstanding
from .models.universe import Universe

logger = logging.getLogger("datawiserai")

_DEFAULT_CACHE_DIR = Path.home() / ".datawiserai" / "cache"

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
        Set to *False* to bypass the local cache entirely.
    """

    def __init__(
        self,
        api_key: str,
        *,
        cache_dir: Union[str, Path, None] = None,
        use_cache: bool = True,
    ) -> None:
        self._api_key = api_key
        self._transport = Transport(api_key)
        self._use_cache = use_cache
        self._cache = FileCache(
            Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        )

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _get_manifest(self, endpoint: str) -> dict:
        return self._transport.get_manifest(endpoint)

    def _fetch(self, endpoint: str, ticker: str) -> dict:
        """Return parsed JSON for *ticker*, using the cache when fresh."""
        manifest = self._get_manifest(endpoint)

        entry = manifest.get(ticker)
        if entry is None:
            raise TickerNotFoundError(ticker, endpoint)

        remote_ts = entry["last_update"]

        if self._use_cache:
            cached_data, cached_ts = self._cache.get(endpoint, ticker)
            if cached_data is not None and cached_ts == remote_ts:
                logger.debug(
                    "cache hit for %s/%s (last_update=%s)",
                    endpoint,
                    ticker,
                    remote_ts,
                )
                return cached_data

        data = self._transport.get(endpoint, ticker)

        if self._use_cache:
            self._cache.put(endpoint, ticker, data, remote_ts)
            logger.debug("cached %s/%s (last_update=%s)", endpoint, ticker, remote_ts)

        return data

    # ------------------------------------------------------------------
    # universe
    # ------------------------------------------------------------------

    def universe(self, endpoint: str) -> Universe:
        """Return the set of available tickers for *endpoint*.

        Parameters
        ----------
        endpoint : str
            One of ``'free-float'``, ``'free-float-events'``,
            ``'shares-outstanding'``, or ``'reference'``.
            Underscore variants (e.g. ``'free_float'``) are also accepted.

        Returns
        -------
        Universe
            Object with ``.tickers``, ``.to_dataframe()``, and ``in``
            membership testing.
        """
        endpoint = _resolve_endpoint(endpoint)
        manifest = self._get_manifest(endpoint)
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

    def clear_cache(self, endpoint: str | None = None) -> int:
        """Remove cached data.  Returns the number of files deleted.

        Parameters
        ----------
        endpoint : str, optional
            Limit clearing to a single endpoint (e.g. ``"free-float"``).
            If omitted the entire cache is wiped.
        """
        if endpoint is not None:
            endpoint = _resolve_endpoint(endpoint)
        return self._cache.clear(endpoint)
