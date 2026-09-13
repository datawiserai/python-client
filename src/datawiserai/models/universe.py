from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .._exceptions import ManifestCompatibilityError


@dataclass(frozen=True)
class UniverseEntry:
    """One security in an endpoint's manifest."""

    ticker: str
    security_id: str
    last_update: str
    doc_last_update: Optional[str] = None


@dataclass(frozen=True)
class Universe:
    """The set of available tickers for an endpoint.

    Returned by :meth:`Client.universe`.
    """

    endpoint: str
    entries: tuple[UniverseEntry, ...]

    @classmethod
    def _from_manifest(
        cls, endpoint: str, manifest: dict[str, Any]
    ) -> Universe:
        if not isinstance(manifest, dict):
            raise ManifestCompatibilityError(
                endpoint, "expected a dictionary of public update entries"
            )

        grouped: dict[str, list[dict[str, Any]]] = {}
        for key, val in manifest.items():
            if not isinstance(key, str) or not isinstance(val, dict):
                raise ManifestCompatibilityError(
                    endpoint,
                    "expected every public update entry to be an object",
                )

            security_id = val.get("security_id")
            if not isinstance(security_id, str) or not security_id:
                raise ManifestCompatibilityError(
                    endpoint,
                    "an update entry is missing its durable security_id",
                )

            ticker = val.get("ticker", "")
            if ticker is None:
                ticker = ""
            if not isinstance(ticker, str):
                raise ManifestCompatibilityError(
                    endpoint, "an update entry has a non-string ticker"
                )

            last_update = val.get("last_update")
            if not isinstance(last_update, str):
                raise ManifestCompatibilityError(
                    endpoint, "an update entry is missing last_update"
                )

            doc_last_update = val.get("doc_last_update")
            if doc_last_update is not None and not isinstance(
                doc_last_update, str
            ):
                raise ManifestCompatibilityError(
                    endpoint,
                    "an update entry has a non-string doc_last_update",
                )

            grouped.setdefault(security_id, []).append(
                {
                    "ticker": ticker,
                    "last_update": last_update,
                    "doc_last_update": doc_last_update,
                }
            )

        entries: list[UniverseEntry] = []
        for security_id, aliases in grouped.items():
            tickers = {entry["ticker"] for entry in aliases if entry["ticker"]}
            last_updates = {entry["last_update"] for entry in aliases}
            doc_last_updates = {
                entry["doc_last_update"]
                for entry in aliases
                if entry["doc_last_update"] is not None
            }
            if len(tickers) > 1:
                raise ManifestCompatibilityError(
                    endpoint,
                    "aliases for one security_id disagree on the current ticker",
                )
            if len(last_updates) > 1 or len(doc_last_updates) > 1:
                raise ManifestCompatibilityError(
                    endpoint,
                    "aliases for one security_id disagree on update metadata",
                )

            entries.append(
                UniverseEntry(
                    ticker=next(iter(tickers), ""),
                    security_id=security_id,
                    last_update=next(iter(last_updates)),
                    doc_last_update=next(iter(doc_last_updates), None),
                )
            )
        entries.sort(key=lambda e: (not e.ticker, e.ticker, e.security_id))
        return cls(endpoint=endpoint, entries=tuple(entries))

    @property
    def tickers(self) -> list[str]:
        """Sorted list of available tickers."""
        return [e.ticker for e in self.entries if e.ticker]

    def to_dataframe(self):
        """Convert to a :class:`pandas.DataFrame`."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required for to_dataframe(). "
                "Install it with:  pip install 'datawiserai[pandas]'"
            ) from None

        rows = [
            {
                "ticker": e.ticker,
                "security_id": e.security_id,
                "last_update": e.last_update,
                "doc_last_update": e.doc_last_update,
            }
            for e in self.entries
        ]
        return pd.DataFrame(rows)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __contains__(self, ticker: str) -> bool:
        return any(
            (bool(e.ticker) and e.ticker == ticker) or e.security_id == ticker
            for e in self.entries
        )

    def __repr__(self) -> str:
        return (
            f"Universe(endpoint={self.endpoint!r}, "
            f"tickers={self.tickers!r})"
        )
