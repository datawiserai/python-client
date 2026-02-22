from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence


@dataclass(frozen=True)
class FreeFloatEvent:
    """A single point-in-time free-float observation."""

    as_of: date
    free_float_factor: float
    free_float_pct: float
    shares_outstanding: float
    excluded_shares: float

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> FreeFloatEvent:
        return cls(
            as_of=date.fromisoformat(d["asOf"]),
            free_float_factor=d["freeFloatFactor"],
            free_float_pct=d["freeFloatPct"],
            shares_outstanding=d["sharesOutstanding"],
            excluded_shares=d["excludedShares"],
        )


@dataclass(frozen=True)
class FreeFloat:
    """Free-float data for a single security.

    Attributes
    ----------
    ticker : str
        Exchange ticker symbol.
    security_id : str
        Datawiser security identifier.
    events : tuple[FreeFloatEvent, ...]
        Time-series of free-float observations, most-recent first.
    """

    ticker: str
    security_id: str
    events: tuple[FreeFloatEvent, ...]

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> FreeFloat:
        return cls(
            ticker=d["ticker"],
            security_id=d["securityId"],
            events=tuple(FreeFloatEvent._from_dict(e) for e in d["events"]),
        )

    def to_dataframe(self, sort: bool = True):
        """Convert to a :class:`pandas.DataFrame`.

        Parameters
        ----------
        sort : bool
            If *True* (default) rows are sorted descending by date
            (most recent first), matching the API order.

        Returns
        -------
        pandas.DataFrame
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
                "free_float_factor": e.free_float_factor,
                "free_float_pct": e.free_float_pct,
                "shares_outstanding": e.shares_outstanding,
                "excluded_shares": e.excluded_shares,
            }
            for e in self.events
        ]
        df = pd.DataFrame(rows)
        if not df.empty:
            df["as_of"] = pd.to_datetime(df["as_of"])
            if sort:
                df = df.sort_values("as_of", ascending=False).reset_index(drop=True)
        return df

    def latest(self) -> FreeFloatEvent | None:
        """Return the most-recent event, or *None* if there are no events."""
        if not self.events:
            return None
        return max(self.events, key=lambda e: e.as_of)

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self):
        return iter(self.events)
