from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


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
        seen: set[str] = set()
        entries: list[UniverseEntry] = []
        for _key, val in manifest.items():
            sid = val.get("security_id", "")
            if sid in seen:
                continue
            seen.add(sid)
            entries.append(
                UniverseEntry(
                    ticker=val["ticker"],
                    security_id=sid,
                    last_update=val["last_update"],
                    doc_last_update=val.get("doc_last_update"),
                )
            )
        entries.sort(key=lambda e: e.ticker)
        return cls(endpoint=endpoint, entries=tuple(entries))

    @property
    def tickers(self) -> list[str]:
        """Sorted list of available tickers."""
        return [e.ticker for e in self.entries]

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
        return any(e.ticker == ticker or e.security_id == ticker for e in self.entries)

    def __repr__(self) -> str:
        return (
            f"Universe(endpoint={self.endpoint!r}, "
            f"tickers={self.tickers!r})"
        )
