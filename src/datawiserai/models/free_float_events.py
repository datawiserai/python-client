from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Sequence


# ---------------------------------------------------------------------------
# High-level event summary  (one row per event date: asOf, ffFactor, etc.)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FreeFloatEventSummary:
    """High-level aggregate for a single free-float event date.

    One row per event: as_of, excluded_shares, ff_factor, delta_shares,
    delta_ff_factor, delta_fff_bps, is_rebal, shares_out.  Fields may be None
    if not present in the API.  delta_fff_bps is (delta_ff_factor / ff_factor)
    * 10000 (basis points).  is_rebal is True when the event reflects a
    rebalance (e.g. new DEF 14A).
    """

    as_of: date
    excluded_shares: Optional[float] = None
    ff_factor: Optional[float] = None
    delta_shares: Optional[float] = None
    delta_ff_factor: Optional[float] = None
    delta_fff_bps: Optional[float] = None
    is_rebal: bool = False
    shares_out: Optional[float] = None

    @classmethod
    def _from_event(cls, ev: dict[str, Any]) -> FreeFloatEventSummary:
        ff = ev.get("ffFactor")
        dff = ev.get("deltaFfFactor")
        delta_fff_bps = None
        if ff is not None and dff is not None and ff != 0:
            delta_fff_bps = (dff / ff) * 100 * 100
        return cls(
            as_of=date.fromisoformat(ev["asOf"]),
            excluded_shares=ev.get("excludedShares"),
            ff_factor=ff,
            delta_shares=ev.get("deltaShares"),
            delta_ff_factor=dff,
            delta_fff_bps=delta_fff_bps,
            is_rebal=ev.get("isRebalanced", False),
            shares_out=ev.get("sharesOut"),
        )


# ---------------------------------------------------------------------------
# Simple / flat view  (first-level components only, no nested drill-down)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FreeFloatOwnerSummary:
    """Flat summary of one owner within a single free-float event date.

    Only scalar fields from the top level of each component are kept;
    nested ``components``, ``restrictions``, ``options``, and
    ``eventDetails`` are omitted.
    """

    as_of: date
    owner_identity_id: str
    name: str
    shares: float
    delta_shares: float
    entity_type: str
    rel_type: str
    event_mask: int
    is_officer: bool
    is_extra_owner: bool
    is_new_owner: bool
    incomplete_event: bool
    filing_date: Optional[str] = None
    source_event: Optional[str] = None
    event_id: Optional[str] = None

    @classmethod
    def _from_component(
        cls, as_of: date, owner_id: str, c: dict[str, Any]
    ) -> FreeFloatOwnerSummary:
        return cls(
            as_of=as_of,
            owner_identity_id=owner_id,
            name=c.get("name", ""),
            shares=c.get("shares", 0.0),
            delta_shares=c.get("deltaShares", 0.0),
            entity_type=c.get("entityType", ""),
            rel_type=c.get("relType", ""),
            event_mask=c.get("eventMask", 0),
            is_officer=c.get("isOfficer", False),
            is_extra_owner=c.get("isExtraOwner", False),
            is_new_owner=c.get("isNewOwner", False),
            incomplete_event=c.get("incompleteEvent", False),
            filing_date=c.get("filingDate"),
            source_event=c.get("sourceEvent"),
            event_id=c.get("id"),
        )


@dataclass(frozen=True)
class FreeFloatEvents:
    """Flat (summary) view of free-float events for a single security.

    Provides:
    - :attr:`event_summaries`: high-level one-row-per-date (asOf, ffFactor,
      excludedShares, deltaShares, deltaFfFactor, sharesOut). Use
      :meth:`to_event_summary_dataframe` for a DataFrame.
    - :attr:`owners`: one row per owner per date. Use :meth:`to_dataframe`.
    """

    ticker: str
    security_id: str
    event_summaries: tuple[FreeFloatEventSummary, ...]
    owners: tuple[FreeFloatOwnerSummary, ...]

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> FreeFloatEvents:
        event_summaries_list: list[FreeFloatEventSummary] = []
        owner_summaries: list[FreeFloatOwnerSummary] = []
        for ev in d.get("events", []):
            ev_date = date.fromisoformat(ev["asOf"])
            event_summaries_list.append(FreeFloatEventSummary._from_event(ev))
            for owner_id, comp in ev.get("components", {}).items():
                owner_summaries.append(
                    FreeFloatOwnerSummary._from_component(
                        ev_date, owner_id, comp
                    )
                )
        return cls(
            ticker=d["ticker"],
            security_id=d["securityId"],
            event_summaries=tuple(event_summaries_list),
            owners=tuple(owner_summaries),
        )

    def to_event_summary_dataframe(self, sort: bool = True):
        """High-level event summary — one row per event date.

        Columns: as_of, excluded_shares, ff_factor, delta_shares,
        delta_ff_factor, shares_out.  Sorted descending by as_of when
        *sort* is True.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required for to_event_summary_dataframe(). "
                "Install it with:  pip install 'datawiserai[pandas]'"
            ) from None

        rows = [
            {
                "as_of": e.as_of,
                "excluded_shares": e.excluded_shares,
                "ff_factor": e.ff_factor,
                "delta_shares": e.delta_shares,
                "delta_ff_factor": e.delta_ff_factor,
                "delta_fff_bps": e.delta_fff_bps,
                "is_rebal": e.is_rebal,
                "shares_out": e.shares_out,
            }
            for e in self.event_summaries
        ]
        df = pd.DataFrame(rows)
        if not df.empty:
            df["as_of"] = pd.to_datetime(df["as_of"])
            if sort:
                df = df.sort_values("as_of", ascending=False).reset_index(drop=True)
        return df

    def to_dataframe(self, sort: bool = True):
        """Flat DataFrame — one row per owner per event date.

        Parameters
        ----------
        sort : bool
            If *True* (default) rows are sorted descending by date
            (most recent first), then by owner name, matching the API order.
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
                "as_of": o.as_of,
                "owner_identity_id": o.owner_identity_id,
                "name": o.name,
                "shares": o.shares,
                "delta_shares": o.delta_shares,
                "entity_type": o.entity_type,
                "rel_type": o.rel_type,
                "event_mask": o.event_mask,
                "is_officer": o.is_officer,
                "is_extra_owner": o.is_extra_owner,
                "is_new_owner": o.is_new_owner,
                "incomplete_event": o.incomplete_event,
                "filing_date": o.filing_date,
                "source_event": o.source_event,
                "event_id": o.event_id,
            }
            for o in self.owners
        ]
        df = pd.DataFrame(rows)
        if not df.empty:
            df["as_of"] = pd.to_datetime(df["as_of"])
            if sort:
                df = df.sort_values(
                    ["as_of", "name"], ascending=[False, True]
                ).reset_index(drop=True)
        return df

    def dates(self) -> list[date]:
        """Return the distinct event dates, most-recent first."""
        return sorted({o.as_of for o in self.owners}, reverse=True)

    def __len__(self) -> int:
        return len(self.owners)

    def __iter__(self):
        return iter(self.owners)


# ---------------------------------------------------------------------------
# Full / drill-down view  (preserves entire nested structure)
# ---------------------------------------------------------------------------

@dataclass
class FreeFloatEventDetail:
    """Full detail for a single event date.

    Attributes
    ----------
    as_of : date
        The event date.
    security_id : str
    components : dict[str, dict]
        Keyed by ``ownerIdentityId``.  Each value is the raw JSON dict
        for that owner **including** nested ``components``, ``restrictions``,
        ``options``, and ``eventDetails``.
    raw : dict
        The original top-level event dict.
    """

    as_of: date
    security_id: str
    components: dict[str, dict] = field(repr=False)
    raw: dict = field(repr=False)

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> FreeFloatEventDetail:
        return cls(
            as_of=date.fromisoformat(d["asOf"]),
            security_id=d.get("securityId", ""),
            components=d.get("components", {}),
            raw=d,
        )

    @property
    def owner_ids(self) -> list[str]:
        return list(self.components.keys())

    @property
    def owner_names(self) -> dict[str, str]:
        """Mapping of ``ownerIdentityId`` → display name."""
        return {
            oid: c.get("name", "")
            for oid, c in self.components.items()
        }

    def owner(self, owner_id: str) -> dict[str, Any]:
        """Return the full raw dict for a single owner."""
        return self.components[owner_id]


@dataclass
class FreeFloatEventsDetail:
    """Full nested drill-down for free-float events.

    This is **not** intended for DataFrames — use it to explore the
    full ownership structure interactively.

    Usage::

        detail = client.free_float_events_detail("OLP")
        detail.dates                        # list of event dates
        ev = detail[0]                      # FreeFloatEventDetail for first date
        ev.owner_names                      # {id: "Smith John", ...}
        ev.owner("OLPzC085ekEKV")           # full raw dict for that owner
        ev.owner("OLPzC085ekEKV")["components"]      # nested sub-components
        ev.owner("OLPzC085ekEKV")["restrictions"]     # nested restrictions
        ev.owner("OLPzC085ekEKV")["eventDetails"]     # nested event details
    """

    ticker: str
    security_id: str
    events: tuple[FreeFloatEventDetail, ...]

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> FreeFloatEventsDetail:
        return cls(
            ticker=d["ticker"],
            security_id=d["securityId"],
            events=tuple(
                FreeFloatEventDetail._from_dict(ev)
                for ev in d.get("events", [])
            ),
        )

    @property
    def dates(self) -> list[date]:
        """Distinct event dates, most-recent first."""
        return sorted({ev.as_of for ev in self.events}, reverse=True)

    def by_date(self, as_of: date | str) -> FreeFloatEventDetail | None:
        """Look up the event for a specific date."""
        if isinstance(as_of, str):
            as_of = date.fromisoformat(as_of)
        for ev in self.events:
            if ev.as_of == as_of:
                return ev
        return None

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, idx: int) -> FreeFloatEventDetail:
        return self.events[idx]

    def __iter__(self):
        return iter(self.events)
