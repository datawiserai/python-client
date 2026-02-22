from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional


@dataclass(frozen=True)
class SharesOutstandingEvent:
    """A single shares-outstanding observation."""

    as_of: date
    share_type: str
    shares: float
    source: str
    sec_type: str
    last_update: str
    as_of_rs: Optional[date] = None

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> SharesOutstandingEvent:
        # Support both asOf and asOfDate for backward compatibility
        as_of_raw = d.get("asOf") or d.get("asOfDate")
        rs = d.get("asOfDateRs")
        return cls(
            as_of=date.fromisoformat(as_of_raw),
            share_type=d["shareType"],
            shares=d["shares"],
            source=d["source"],
            sec_type=d["secType"],
            last_update=d["lastUpdate"],
            as_of_rs=date.fromisoformat(rs) if rs else None,
        )


@dataclass(frozen=True)
class SharesOutstanding:
    """Shares-outstanding data for a single security.

    Attributes
    ----------
    ticker : str
        Exchange ticker symbol.
    security_id : str
        Datawiser security identifier.
    events : tuple[SharesOutstandingEvent, ...]
        Time-series of share-count observations, most-recent first.
    """

    ticker: str
    security_id: str
    events: tuple[SharesOutstandingEvent, ...]

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> SharesOutstanding:
        return cls(
            ticker=d["ticker"],
            security_id=d["securityId"],
            events=tuple(
                SharesOutstandingEvent._from_dict(e) for e in d["events"]
            ),
        )

    def to_dataframe(self, sort: bool = True):
        """Convert to a :class:`pandas.DataFrame`.

        Parameters
        ----------
        sort : bool
            If *True* (default) rows are sorted descending by ``as_of``
            (most recent first), matching the API order.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required for to_dataframe(). "
                "Install it with:  pip install 'datawiserai[pandas]'"
            ) from None

        rows = [
            {
                "as_of": e.as_of,
                "share_type": e.share_type,
                "shares": e.shares,
                "source": e.source,
                "sec_type": e.sec_type,
                "last_update": e.last_update,
                "as_of_rs": e.as_of_rs,
            }
            for e in self.events
        ]
        df = pd.DataFrame(rows)
        if not df.empty:
            df["as_of"] = pd.to_datetime(df["as_of"])
            df["as_of_rs"] = pd.to_datetime(df["as_of_rs"])
            if sort:
                df = df.sort_values("as_of", ascending=False).reset_index(drop=True)
        return df

    def latest(self) -> SharesOutstandingEvent | None:
        """Return the most-recent event, or *None* if there are no events."""
        if not self.events:
            return None
        return max(self.events, key=lambda e: e.as_of)

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self):
        return iter(self.events)
