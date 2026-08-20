from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from datawiserai.models.free_float_events import (
    FreeFloatEvents,
    FreeFloatEventsDetail,
    OwnerCarrierHandoff,
    expand_free_float_events_payload,
)

FULL_BUILD_FIXTURE_PAIRS = (
    # tickers included in 'free' tier
    ("GOOGL", "GOOGLLEDOJy"),
    ("AAPL", "AAPLRkNMQu0"),
    ("OLP", "OLPzCBoCCmy"),
    # other ad-hoc tickers  
    ("VAL", "VALXHsJANS0"),  # one of the smaller files
    ("UTHR", "UTHREUOlKij"), # one of the larger files
)


def _component(owner_id: str, shares: float, **extra):
    return {
        "asOf": extra.pop("asOf", "2024-01-01"),
        "ownerIdentityId": owner_id,
        "name": f"Owner {owner_id}",
        "shares": shares,
        "deltaShares": extra.pop("deltaShares", 0.0),
        "entityType": "individual",
        "relType": "direct",
        "eventMask": extra.pop("eventMask", 1),
        **extra,
    }


def _delta_payload():
    return {
        "ticker": "XYZ",
        "securityId": "XYZsec",
        "eventFormat": "free_float_events_delta_v1",
        "eventSort": "as_of_ascending",
        "deltaFields": {
            "components": "componentDeletes",
            "ownerIdentitiesMap": "ownerIdentityDeletes",
            "knownCrossHoldings": "knownCrossHoldingDeletes",
        },
        # Intentionally out of order: the decoder must replay by asOf ascending.
        "events": [
            {
                "asOf": "2024-01-02",
                "securityId": "XYZsec",
                "ffFactor": 0.74,
                "sharesOut": 1000,
                "excludedShares": 260,
                "deltaShares": 10,
                "deltaFfFactor": -0.01,
                "components": {
                    # Whole-record replacement: the seed's filingDate must not
                    # survive when B is replaced here.
                    "B": _component("B", 30, asOf="2024-01-01"),
                },
            },
            {
                "asOf": "2024-01-01",
                "securityId": "XYZsec",
                "ffFactor": 0.75,
                "sharesOut": 1000,
                "excludedShares": 250,
                "deltaShares": 0,
                "deltaFfFactor": 0,
                "components": {
                    "A": _component(
                        "A",
                        10,
                        options=[],
                        restrictions=[],
                    ),
                    "B": _component(
                        "B",
                        20,
                        filingDate="2024-01-01",
                        options=[],
                        restrictions=[],
                    ),
                },
                "ownerIdentitiesMap": {
                    "A": {"namePrimary": "Owner A"},
                    "B": {"namePrimary": "Owner B"},
                },
                "knownCrossHoldings": {"KH1": {"source": "seed"}},
            },
            {
                "asOf": "2024-01-03",
                "securityId": "XYZsec",
                "ffFactor": 0.755,
                "sharesOut": 1000,
                "excludedShares": 245,
                "deltaShares": -15,
                "deltaFfFactor": 0.015,
                "components": {
                    "C": _component("C", 5, asOf="2024-01-03"),
                },
                "componentDeletes": ["B"],
                "componentDeleteDetails": {
                    "B": {
                        "sourceEvent": "schedule13d|2024-01-03|accession|DOC",
                        "replacementOwnerIdentityId": "C",
                        "reportedSharesAfter": 5,
                        "excludedSharesAfter": 0,
                        "entityTypeAfter": "organization",
                        "reason": "linked_owner_below_threshold",
                    }
                },
                "ownerIdentitiesMap": {
                    "C": {"namePrimary": "Owner C"},
                },
                "ownerIdentityDeletes": ["B"],
                "knownCrossHoldingDeletes": ["KH1"],
            },
        ],
    }


def test_expand_free_float_events_payload_replays_seed_delta_and_deletes():
    expanded = expand_free_float_events_payload(_delta_payload())
    events = expanded["events"]

    assert "eventFormat" not in expanded
    assert "eventSort" not in expanded
    assert "deltaFields" not in expanded

    assert [event["asOf"] for event in events] == [
        "2024-01-01",
        "2024-01-02",
        "2024-01-03",
    ]

    assert set(events[0]["components"]) == {"A", "B"}
    assert events[0]["components"]["B"]["shares"] == 20
    assert "componentDeleteDetails" not in events[0]

    assert set(events[1]["components"]) == {"A", "B"}
    assert events[1]["components"]["A"]["shares"] == 10
    assert events[1]["components"]["B"]["shares"] == 30
    assert "filingDate" not in events[1]["components"]["B"]
    assert set(events[1]["ownerIdentitiesMap"]) == {"A", "B"}
    assert events[1]["knownCrossHoldings"] == {"KH1": {"source": "seed"}}
    assert "componentDeleteDetails" not in events[1]

    assert set(events[2]["components"]) == {"A", "C"}
    assert set(events[2]["ownerIdentitiesMap"]) == {"A", "C"}
    assert events[2]["knownCrossHoldings"] == {}
    assert events[2]["componentDeleteDetails"] == {
        "B": {
            "sourceEvent": "schedule13d|2024-01-03|accession|DOC",
            "replacementOwnerIdentityId": "C",
            "reportedSharesAfter": 5,
            "excludedSharesAfter": 0,
            "entityTypeAfter": "organization",
            "reason": "linked_owner_below_threshold",
        }
    }
    assert "componentDeletes" not in events[2]
    assert "ownerIdentityDeletes" not in events[2]
    assert "knownCrossHoldingDeletes" not in events[2]


def test_free_float_events_models_use_expanded_latest_first_events():
    events = FreeFloatEvents._from_dict(_delta_payload())
    assert [summary.as_of for summary in events.event_summaries] == [
        date(2024, 1, 3),
        date(2024, 1, 2),
        date(2024, 1, 1),
    ]

    owners_by_date = {}
    for owner in events.owners:
        owners_by_date.setdefault(owner.as_of, {})[owner.owner_identity_id] = owner

    assert set(owners_by_date[date(2024, 1, 2)]) == {"A", "B"}
    assert owners_by_date[date(2024, 1, 2)]["B"].shares == 30
    assert set(owners_by_date[date(2024, 1, 3)]) == {"A", "C"}

    detail = FreeFloatEventsDetail._from_dict(_delta_payload())
    assert [event.as_of for event in detail.events] == [
        date(2024, 1, 3),
        date(2024, 1, 2),
        date(2024, 1, 1),
    ]

    owner_c = detail.events[0].owner("C")
    assert owner_c.options == []
    assert owner_c.restrictions == []

    deleted_b = detail.events[0].component_delete_details["B"]
    assert deleted_b.source_event == "schedule13d|2024-01-03|accession|DOC"
    assert deleted_b.replacement_owner_identity_id == "C"
    assert deleted_b.reported_shares_after == 5
    assert deleted_b.excluded_shares_after == 0
    assert deleted_b.entity_type_after == "organization"
    assert deleted_b.reason == "linked_owner_below_threshold"

    owner_b = detail.by_date("2024-01-02").owner("B")
    assert owner_b.shares == 30
    assert owner_b.options == []
    assert owner_b.restrictions == []
    assert detail.by_date("2024-01-02").component_delete_details == {}


def test_component_detail_extracts_restricted_stock_flag():
    payload = {
        "ticker": "XYZ",
        "securityId": "XYZsec",
        "events": [
            {
                "asOf": "2024-01-01",
                "components": {
                    "A": _component(
                        "A",
                        10,
                        components=[
                            {"id": "restricted", "isRestrictedStock": True},
                            {"id": "not-restricted", "isRestrictedStock": False},
                            {"id": "unknown"},
                        ],
                    )
                },
            }
        ],
    }

    owner = FreeFloatEventsDetail._from_dict(payload)[0].owner("A")

    assert owner.components[0].is_restricted_stock is True
    assert owner.components[1].is_restricted_stock is False
    assert owner.components[2].is_restricted_stock is None


def test_component_detail_preserves_adjusted_and_reported_share_positions():
    payload = {
        "ticker": "XYZ",
        "securityId": "XYZsec",
        "events": [
            {
                "asOf": "2025-08-03",
                "components": {
                    "A": _component(
                        "A",
                        10,
                        components=[
                            {
                                "shares": 0,
                                "adjustedShares": 96_000_000,
                                "adjustedDeltaShares": 96_000_000,
                                "reportedShares": 96_000_000,
                                "reportedDeltaShares": 96_000_000,
                                "isRestrictedStock": True,
                            }
                        ],
                    )
                },
            }
        ],
    }

    component = FreeFloatEventsDetail._from_dict(payload)[0].owner(
        "A"
    ).components[0]

    assert component.shares == 0
    assert component.adjusted_shares == 96_000_000
    assert component.adjusted_delta_shares == 96_000_000
    assert component.reported_shares == 96_000_000
    assert component.reported_delta_shares == 96_000_000


def test_synthetic_remainder_metadata_is_preserved_in_both_views():
    payload = {
        "ticker": "XYZ",
        "securityId": "XYZsec",
        "events": [
            {
                "asOf": "2025-12-12",
                "components": {
                    "synthetic_remainder": _component(
                        "synthetic_remainder",
                        -1_009_446,
                        entityType="group_total",
                        filingDate="2025-12-15",
                        isSyntheticRemainder=True,
                        syntheticRemainderLabel="Synthetic Remainder (-1 insiders)",
                    )
                },
            }
        ],
    }

    flat = FreeFloatEvents._from_dict(payload)
    owner = flat.owners[0]
    assert owner.is_synthetic_remainder is True
    assert (
        owner.synthetic_remainder_label
        == "Synthetic Remainder (-1 insiders)"
    )
    assert flat.to_dataframe().loc[0, "is_synthetic_remainder"]

    detail_owner = FreeFloatEventsDetail._from_dict(payload)[0].owner(
        "synthetic_remainder"
    )
    assert detail_owner.is_synthetic_remainder is True
    assert (
        detail_owner.synthetic_remainder_label
        == "Synthetic Remainder (-1 insiders)"
    )


def test_legacy_owner_has_no_synthetic_remainder_flag():
    payload = {
        "ticker": "XYZ",
        "securityId": "XYZsec",
        "events": [
            {
                "asOf": "2024-01-01",
                "components": {"A": _component("A", 10)},
            }
        ],
    }

    flat_owner = FreeFloatEvents._from_dict(payload).owners[0]
    detail_owner = FreeFloatEventsDetail._from_dict(payload)[0].owner("A")

    assert flat_owner.is_synthetic_remainder is None
    assert detail_owner.is_synthetic_remainder is None


def test_old_payloads_without_event_format_are_treated_as_full_snapshots():
    payload = {
        "ticker": "XYZ",
        "securityId": "XYZsec",
        "events": [
            {
                "asOf": "2024-01-02",
                "components": {"B": _component("B", 30)},
            },
        ],
    }

    expanded = expand_free_float_events_payload(payload)

    assert expanded == payload
    assert expanded is not payload
    assert expanded["events"] is not payload["events"]


def test_compact_events_normalize_explicit_owner_carrier_handoff():
    payload = {
        "ticker": "BLFS",
        "securityId": "BLFSx0Ds9jh",
        "eventFormat": "free_float_events_delta_v1",
        "eventSort": "as_of_ascending",
        "deltaFields": {
            "components": "componentDeletes",
            "ownerIdentitiesMap": "ownerIdentityDeletes",
            "knownCrossHoldings": "knownCrossHoldingDeletes",
        },
        "events": [
            {
                "asOf": "2025-08-11",
                "components": {
                    "BLFSxxIrQ2rNW": _component(
                        "BLFSxxIrQ2rNW",
                        7_207_165,
                        filingDate="2025-08-13",
                    )
                },
            },
            {
                "asOf": "2025-08-15",
                "componentDeletes": ["BLFSxxIrQ2rNW"],
                "components": {
                    "BLFSxCKWRRlbk": _component(
                        "BLFSxCKWRRlbk",
                        6_707_165,
                        filingDate="2025-08-21",
                    )
                },
                "knownCrossHoldings": {
                    "BLFSxCKWRRlbk": {
                        "ownerIdentityId": "BLFSxCKWRRlbk",
                        "sourceOwnerIdentityId": "BLFSxCKWRRlbk",
                        "linkedOwnerIdentityIds": ["BLFSxxIrQ2rNW"],
                    }
                },
                "ExplicitOwnerLinkSuppressionSources": {
                    "BLFSx21b3FtLY": [
                        "BLFSxCKWRRlbk",
                        "BLFSxxIrQ2rNW",
                    ],
                    "BLFSxxIrQ2rNW": ["BLFSxCKWRRlbk"],
                },
            },
        ],
    }

    ffe = FreeFloatEvents._from_dict(payload)

    assert ffe.owner_carrier_handoffs == (
        OwnerCarrierHandoff(
            as_of=date(2025, 8, 15),
            predecessor_owner_id="BLFSxxIrQ2rNW",
            successor_owner_id="BLFSxCKWRRlbk",
            related_owner_ids=(
                "BLFSx21b3FtLY",
                "BLFSxCKWRRlbk",
                "BLFSxxIrQ2rNW",
            ),
            evidence_sources=(
                "explicit_owner_link_suppression",
                "known_cross_holding_link",
            ),
        ),
    )


def test_carrier_handoff_is_not_inferred_without_explicit_owner_link():
    payload = {
        "ticker": "XYZ",
        "securityId": "XYZsec",
        "eventFormat": "free_float_events_delta_v1",
        "eventSort": "as_of_ascending",
        "events": [
            {
                "asOf": "2025-08-11",
                "components": {"OLD": _component("OLD", 100)},
            },
            {
                "asOf": "2025-08-15",
                "componentDeletes": ["OLD"],
                "components": {"NEW": _component("NEW", 100)},
            },
        ],
    }

    assert FreeFloatEvents._from_dict(payload).owner_carrier_handoffs == ()


def _load_gzip_json(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def test_full_build_fixture_pairs_expand_to_full_payload():
    base = (
        Path(__file__).resolve().parents[2]
        / "datawiser-api"
        / "data"
        / "basic"
        / "free-float-events"
    )

    for ticker, security_id in FULL_BUILD_FIXTURE_PAIRS:
        stem = f"{ticker}-{security_id}"
        compact_path = base / f"{stem}.json.gz"
        full_path = base / f"{stem}-full.json.gz"
        assert compact_path.exists(), f"Missing compact fixture: {compact_path}"
        assert full_path.exists(), f"Missing full fixture: {full_path}"

        compact_payload = _load_gzip_json(compact_path)
        full_payload = _load_gzip_json(full_path)

        assert expand_free_float_events_payload(compact_payload) == full_payload


def _run_direct():
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"{name}: ok")


if __name__ == "__main__":
    _run_direct()
