from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, List, Optional, Sequence, Union


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
# Full / drill-down view  (typed nested structure)
# ---------------------------------------------------------------------------

@dataclass
class Component:
    """One sub-component within an owner's components list."""

    event_mask: int
    rel_type: str
    shares: float
    delta_shares: float
    source_event: str
    id: str
    event_id: str
    is_parent_event: bool
    possible_shared_ownership: bool
    reconciled: bool
    is_ben_owner_exclusion: bool
    is_cross_holding: bool
    retained_from: List[Any]
    incomplete_event_retained: bool
    nature_of_ownership: Optional[str] = None
    entity: Optional[str] = None

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> Component:
        return cls(
            event_mask=d.get("eventMask", 0),
            rel_type=d.get("relType", ""),
            shares=d.get("shares", 0.0),
            delta_shares=d.get("deltaShares", 0.0),
            source_event=d.get("sourceEvent", ""),
            id=d.get("id", ""),
            event_id=d.get("eventId", ""),
            is_parent_event=d.get("isParentEvent", False),
            possible_shared_ownership=d.get("possibleSharedOwnership", False),
            reconciled=d.get("reconciled", False),
            is_ben_owner_exclusion=d.get("isBenOwnerExclusion", False),
            is_cross_holding=d.get("isCrossHolding", False),
            retained_from=d.get("retainedFrom", []),
            incomplete_event_retained=d.get("incompleteEventRetained", False),
            nature_of_ownership=d.get("natureOfOwnership"),
            entity=d.get("entity"),
        )


@dataclass
class Restriction:
    """One restriction within an owner's restrictions list."""

    event_mask: int
    rel_type: str
    shares: float
    delta_shares: float
    source_event: str
    id: str
    event_id: str
    is_parent_event: bool
    is_oversized_shares: bool
    possible_shared_ownership: bool
    reconciled: bool
    is_ben_owner_exclusion: bool
    is_cross_holding: bool
    retained_from: List[Any]
    incomplete_event_retained: bool
    reason: Optional[List[str]] = None
    included_in_total: Optional[bool] = None
    restriction_type: Optional[str] = None

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> Restriction:
        return cls(
            event_mask=d.get("eventMask", 0),
            rel_type=d.get("relType", ""),
            shares=d.get("shares", 0.0),
            delta_shares=d.get("deltaShares", 0.0),
            source_event=d.get("sourceEvent", ""),
            id=d.get("id", ""),
            event_id=d.get("eventId", ""),
            is_parent_event=d.get("isParentEvent", False),
            is_oversized_shares=d.get("isOversizedShares", False),
            possible_shared_ownership=d.get("possibleSharedOwnership", False),
            reconciled=d.get("reconciled", False),
            is_ben_owner_exclusion=d.get("isBenOwnerExclusion", False),
            is_cross_holding=d.get("isCrossHolding", False),
            retained_from=d.get("retainedFrom", []),
            incomplete_event_retained=d.get("incompleteEventRetained", False),
            reason=d.get("reason"),
            included_in_total=d.get("includedInTotal"),
            restriction_type=d.get("restrictionType"),
        )


@dataclass
class Option:
    """One option within an owner's options list."""

    event_mask: Optional[int] = None
    rel_type: Optional[str] = None
    shares: Optional[float] = None
    delta_shares: Optional[float] = None
    source_event: Optional[str] = None
    id: Optional[str] = None
    event_id: Optional[str] = None
    raw: Optional[dict[str, Any]] = field(default=None, repr=False)

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> Option:
        return cls(
            event_mask=d.get("eventMask"),
            rel_type=d.get("relType"),
            shares=d.get("shares"),
            delta_shares=d.get("deltaShares"),
            source_event=d.get("sourceEvent"),
            id=d.get("id"),
            event_id=d.get("eventId"),
            raw=d,
        )


@dataclass
class EventDetails:
    """Event details for an owner (e.g. form type, transaction code)."""

    form_type: Optional[str] = None
    ir_type: Optional[str] = None
    notes: Optional[str] = None
    rel_type: Optional[str] = None
    file_source: Optional[str] = None
    transaction_date: Optional[str] = None
    shares_owned_post: Optional[float] = None
    delta_shares: Optional[float] = None
    shares: Optional[float] = None
    possible_shared_ownership: Optional[bool] = None
    instrument_type: Optional[str] = None
    instrument_subtype: Optional[str] = None
    is_officer: Optional[bool] = None
    zero_shares_verified: Optional[bool] = None
    llm_sourced: Optional[bool] = None
    raw: Optional[dict[str, Any]] = field(default=None, repr=False)

    @classmethod
    def _from_dict(cls, d: dict[str, Any] | None) -> Optional[EventDetails]:
        if not d:
            return None

        return cls(
            form_type=d.get("formType"),
            ir_type=d.get("irType"),
            notes=d.get("notes"),
            rel_type=d.get("relType"),
            file_source=d.get("fileSource"),
            transaction_date=d.get("transactionDate"),
            shares_owned_post=d.get("sharesOwnedPost"),
            delta_shares=d.get("deltaShares"),
            shares=d.get("shares"),
            possible_shared_ownership=d.get("possibleSharedOwnership"),
            is_officer=d.get("isOfficer"),
            zero_shares_verified=d.get("zeroSharesVerified"),
            llm_sourced=d.get("llmSourced"),
            instrument_type=d.get("instrumentType"),
            instrument_subtype=d.get("instrumentSubtype"),
            raw=d,
        )


@dataclass
class OwnerDetail:
    """Full detail for one owner on an event date.

    Attributes
    ----------
    components : List[Component]
        Nested sub-components.
    restrictions : List[Restriction]
        Restrictions (e.g. restricted stock).
    options : List[Option]
        Option-related entries.
    event_details : EventDetails or None
        Form/transaction event details.
    """

    as_of: date
    owner_identity_id: str
    shares: float
    event_mask: int
    entity_type: str
    delta_shares: float
    rel_type: str
    name: str
    components: List[Component] = field(default_factory=list)
    restrictions: List[Restriction] = field(default_factory=list)
    options: List[Option] = field(default_factory=list)
    event_details: Optional[EventDetails] = None
    filing_date: Optional[str] = None
    source_event: Optional[str] = None
    event_id: Optional[str] = None
    incomplete_event: bool = False
    source_spans_dates: bool = False
    is_officer: bool = False
    is_extra_owner: bool = False
    is_new_owner: bool = False

    @classmethod
    def _from_dict(cls, owner_id: str, d: dict[str, Any]) -> OwnerDetail:
        return cls(
            as_of=date.fromisoformat(d["asOf"]) if d.get("asOf") else date(1970, 1, 1),
            owner_identity_id=owner_id,
            shares=d.get("shares", 0.0),
            event_mask=d.get("eventMask", 0),
            entity_type=d.get("entityType", ""),
            delta_shares=d.get("deltaShares", 0.0),
            rel_type=d.get("relType", ""),
            name=d.get("name", ""),
            components=[Component._from_dict(c) for c in d.get("components", [])],
            restrictions=[Restriction._from_dict(r) for r in d.get("restrictions", [])],
            options=[Option._from_dict(o) for o in d.get("options", [])],
            event_details=EventDetails._from_dict(d.get("eventDetails")),
            filing_date=d.get("filingDate"),
            source_event=d.get("sourceEvent"),
            event_id=d.get("id"),
            incomplete_event=d.get("incompleteEvent", False),
            source_spans_dates=d.get("sourceSpansDates", False),
            is_officer=d.get("isOfficer", False),
            is_extra_owner=d.get("isExtraOwner", False),
            is_new_owner=d.get("isNewOwner", False),
        )


@dataclass
class FreeFloatEventDetail:
    """Full detail for a single event date.

    Attributes
    ----------
    as_of : date
        The event date.
    security_id : str
    components : dict[str, OwnerDetail]
        Keyed by ``ownerIdentityId``.  Each value is a typed :class:`OwnerDetail`
        with :attr:`OwnerDetail.components`, :attr:`OwnerDetail.restrictions`,
        :attr:`OwnerDetail.options`, and :attr:`OwnerDetail.event_details`.
    raw : dict
        The original top-level event dict (for backward compatibility).
    """

    as_of: date
    security_id: str
    components: dict[str, OwnerDetail] = field(repr=False)
    shares_out: float = 0.0
    ff_factor: float = 0.0
    excluded_shares: float = 0.0
    delta_shares: float = 0.0
    delta_ff_factor: float = 0.0
    is_rebal: bool = False
    delta: dict[str, dict[str, Union[float, str]]] = field(default_factory=dict)
    raw: dict = None

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> FreeFloatEventDetail:
        comps = d.get("components", {})
        return cls(
            as_of=date.fromisoformat(d["asOf"]),
            security_id=d.get("securityId", ""),
            components={
                oid: OwnerDetail._from_dict(oid, c)
                for oid, c in comps.items()
            },
            shares_out=d.get("sharesOut", 0.0),
            ff_factor=d.get("ffFactor", 0.0),
            excluded_shares=d.get("excludedShares", 0.0),
            delta_shares=d.get("deltaShares", 0.0),
            delta_ff_factor=d.get("deltaFfFactor", 0.0),
            delta=d.get("delta", {}),
            is_rebal=d.get("isRebalanced", False),
            raw=d,
        )

    @property
    def owner_ids(self) -> list[str]:
        return list(self.components.keys())

    @property
    def owner_names(self) -> dict[str, str]:
        """Mapping of ``ownerIdentityId`` → display name."""
        return {oid: owner.name for oid, owner in self.components.items()}

    def owner(self, owner_id: str) -> OwnerDetail:
        """Return the typed :class:`OwnerDetail` for a single owner."""
        return self.components[owner_id]

    def owner_from_name(self, name: str) -> OwnerDetail:
        return next(owner for owner in self.components.values() if owner.name == name)
        raise ValueError(f"No owner found with name {name}")

    def owner_delta(self, owner_id: str) -> dict[str, Union[float, str]]:
        owner_delta = {}
        for key, value in self.delta.items():
            if owner_id in value:
                owner_delta[key] = value[owner_id]
        return owner_delta


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
        owner = ev.owner("OLPzC085ekEKV")   # OwnerDetail
        owner.components                    # List[Component]
        owner.restrictions                 # List[Restriction]
        owner.options                      # List[Option]
        owner.event_details                # EventDetails or None
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
