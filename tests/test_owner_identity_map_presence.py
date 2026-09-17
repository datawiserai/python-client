"""Empty owner-identity maps follow the independent full producer's contract."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from datawiserai.models.free_float_events import expand_free_float_events_payload


def _canonical(value):
    # Preserve array order and numeric spelling/types when comparing JSON.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _compact_and_independent_full():
    owner = {"O": {"namePrimary": "Owner"}}
    component = {"O": {"shares": 1.0, "nested": {"ownerIdentitiesMap": {}}}}
    compact = {
        "ticker": "SYNTHETIC",
        "eventFormat": "free_float_events_delta_v1",
        "eventSort": "as_of_ascending",
        "events": [
            {"asOf": "2026-01-01", "deltaSharesBasisAdjustment": 1.0},
            {"asOf": "2026-01-02", "components": deepcopy(component),
             "ownerIdentitiesMap": deepcopy(owner), "deltaSharesBasisAdjustment": 2.0},
            {"asOf": "2026-01-03"},
            {"asOf": "2026-01-04", "componentDeletes": ["O"],
             "ownerIdentityDeletes": ["O"],
             "componentDeleteDetails": {"O": {"reason": "true_exit", "reportedSharesAfter": 0.0}}},
            {"asOf": "2026-01-05", "components": deepcopy(component),
             "ownerIdentitiesMap": deepcopy(owner)},
        ],
    }
    # Independent explicit full expectation: never generated with the decoder.
    full = {"ticker": "SYNTHETIC", "events": [
        {"asOf": "2026-01-01", "components": {}, "knownCrossHoldings": {},
         "deltaSharesBasisAdjustment": 1.0},
        {"asOf": "2026-01-02", "components": deepcopy(component),
         "ownerIdentitiesMap": deepcopy(owner), "knownCrossHoldings": {},
         "deltaSharesBasisAdjustment": 2.0},
        {"asOf": "2026-01-03", "components": deepcopy(component),
         "ownerIdentitiesMap": deepcopy(owner), "knownCrossHoldings": {}},
        {"asOf": "2026-01-04", "components": {}, "knownCrossHoldings": {}},
        {"asOf": "2026-01-05", "components": deepcopy(component),
         "ownerIdentitiesMap": deepcopy(owner), "knownCrossHoldings": {}},
    ]}
    return compact, full


def _state_and_details(expanded):
    # componentDeleteDetails is event-local audit content, not full replay state.
    details = [(i, deepcopy(event["componentDeleteDetails"]))
               for i, event in enumerate(expanded["events"])
               if "componentDeleteDetails" in event]
    state = {**expanded, "events": [
        {key: value for key, value in event.items() if key != "componentDeleteDetails"}
        for event in expanded["events"]
    ]}
    return state, details


def test_empty_seed_arrival_sparse_carry_delete_last_and_reintroduction():
    compact, full = _compact_and_independent_full()
    before = deepcopy(compact)
    expanded = expand_free_float_events_payload(compact)
    state, details = _state_and_details(expanded)
    assert _canonical(state) == _canonical(full)
    assert details == [(3, compact["events"][3]["componentDeleteDetails"])]
    assert [i for i, event in enumerate(expanded["events"])
            if "ownerIdentitiesMap" not in event] == [0, 3]
    assert all(event["knownCrossHoldings"] == {} for event in expanded["events"])
    assert expanded["events"][0]["components"] == {}
    assert compact == before


def test_nested_fields_types_and_returned_maps_are_unchanged_and_isolated():
    compact, _ = _compact_and_independent_full()
    before = deepcopy(compact)
    expanded = expand_free_float_events_payload(compact)
    first = expanded["events"][1]
    assert type(first["components"]["O"]["shares"]) is float
    assert first["components"]["O"]["nested"]["ownerIdentitiesMap"] == {}
    assert type(first["deltaSharesBasisAdjustment"]) is float
    first["ownerIdentitiesMap"]["O"]["namePrimary"] = "changed output"
    first["components"]["O"]["nested"]["ownerIdentitiesMap"]["X"] = "changed"
    assert expanded["events"][2]["ownerIdentitiesMap"]["O"]["namePrimary"] == "Owner"
    assert expanded["events"][2]["components"]["O"]["nested"]["ownerIdentitiesMap"] == {}
    assert compact == before


@pytest.mark.parametrize("owner_map", [{}, None, {"O": {}}])
def test_already_full_payload_presence_is_unchanged(owner_map):
    full = {"events": [{"asOf": "2026-01-01", "ownerIdentitiesMap": owner_map}]}
    expanded = expand_free_float_events_payload(full)
    assert _canonical(expanded) == _canonical(full)
    assert expanded["events"] is not full["events"]
    assert expanded["events"][0] is not full["events"][0]


def test_custom_delta_fields_leave_unmanaged_owner_map_unchanged():
    compact = {"eventFormat": "free_float_events_delta_v1",
               "deltaFields": {"components": "componentDeletes"},
               "events": [{"asOf": "2026-01-01", "components": {},
                           "ownerIdentitiesMap": {}, "knownCrossHoldings": None}]}
    assert expand_free_float_events_payload(compact)["events"] == compact["events"]


@pytest.mark.parametrize("fault", ["value", "numeric_type", "delete_id", "delete_timing", "owner_loss"])
def test_corrupted_compact_state_still_differs_from_independent_full(fault):
    compact, full = _compact_and_independent_full()
    if fault == "value":
        compact["events"][1]["components"]["O"]["shares"] = 999.0
    elif fault == "numeric_type":
        compact["events"][1]["components"]["O"]["shares"] = 1
    elif fault == "delete_id":
        compact["events"][3]["ownerIdentityDeletes"] = ["OTHER"]
    elif fault == "delete_timing":
        compact["events"][2]["ownerIdentityDeletes"] = compact["events"][3].pop("ownerIdentityDeletes")
    elif fault == "owner_loss":
        compact["events"][1].pop("ownerIdentitiesMap")
    state, _ = _state_and_details(expand_free_float_events_payload(compact))
    assert _canonical(state) != _canonical(full)
