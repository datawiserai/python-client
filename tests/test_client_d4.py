"""Real-client tests against a synthetic, no-socket D4-L HTTP contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

import pytest
from requests import PreparedRequest, Response
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from datawiserai import (
    Client,
    DatawiserCacheError,
    DatawiserNotFoundError,
    DatawiserResponseError,
    FreeFloat,
    FreeFloatEvents,
    FreeFloatEventsDetail,
    ManifestCompatibilityError,
    Reference,
    SharesOutstanding,
)


SYNTHETIC_BASE = "https://synthetic.datawiser.test/v1"
SYNTHETIC_KEY = "pk_live_synthetic_contract_only"


def _so_payload(identifier, security_id, shares=100.0, ticker=None):
    return {
        "ticker": ticker or identifier,
        "securityId": security_id,
        "events": [
            {
                "asOf": "2026-01-01",
                "shareType": "issued",
                "shares": shares,
                "source": "synthetic-contract-fixture",
                "secType": "Common Stock",
                "lastUpdate": "equal-last-update",
            }
        ],
    }


def _reference_payload(identifier, security_id, marker="basic"):
    return {
        "ticker": identifier,
        "securityId": security_id,
        "companyId": f"company-{security_id}",
        "listingId": f"listing-{security_id}",
        "profile": marker,
    }


def _free_float_payload(identifier, security_id, factor=0.75):
    return {
        "ticker": identifier,
        "securityId": security_id,
        "events": [
            {
                "asOf": "2026-01-01",
                "freeFloatFactor": factor,
                "freeFloatPct": factor * 100,
                "sharesOutstanding": 1000,
                "excludedShares": 1000 * (1 - factor),
            }
        ],
    }


def _ffe_payload(identifier, security_id):
    return {
        "ticker": identifier,
        "securityId": security_id,
        "events": [],
    }


def _http_response(
    request: PreparedRequest,
    status: int,
    *,
    data=None,
    headers=None,
) -> Response:
    response = Response()
    response.status_code = status
    response.request = request
    response.url = request.url
    response.headers = CaseInsensitiveDict(headers or {})
    response._content = b"" if data is None else json.dumps(data).encode("utf-8")
    response.encoding = "utf-8"
    return response


class SyntheticContractAPI(BaseAdapter):
    """Deterministic in-process contract fixture; never opens a socket."""

    def __init__(self):
        self.resources = {}
        self.manifests = {}
        self.calls = []
        self.data_status_counts = {200: 0, 304: 0}
        self.manifest_requests = 0
        self.before_conditional_304 = None

    def set_resource(
        self,
        dataset,
        identifier,
        payload,
        *,
        etag='"v1"',
        status=200,
        always_304=False,
        response_304_etag=None,
    ):
        self.resources[(dataset, identifier)] = {
            "payload": payload,
            "etag": etag,
            "status": status,
            "always_304": always_304,
            "response_304_etag": response_304_etag,
        }

    def send(self, request, **_kwargs):
        raw_segments = urlsplit(request.url).path.strip("/").split("/")
        if len(raw_segments) >= 3 and raw_segments[-1] == "updates":
            dataset = unquote(raw_segments[-2])
            self.manifest_requests += 1
            self.calls.append(
                {
                    "kind": "manifest",
                    "dataset": dataset,
                    "request": request,
                    "status": 200,
                }
            )
            return _http_response(
                request,
                200,
                data=self.manifests.get(dataset, {}),
                headers={"Content-Type": "application/json"},
            )

        dataset = unquote(raw_segments[-2])
        identifier = unquote(raw_segments[-1])
        spec = self.resources.get((dataset, identifier))
        conditional = request.headers.get("If-None-Match")
        if spec is None:
            status = 404
            response = _http_response(request, status)
        elif spec["status"] != 200:
            status = spec["status"]
            response = _http_response(request, status)
        elif spec["always_304"] or (
            conditional is not None and conditional == spec["etag"]
        ):
            status = 304
            hook = self.before_conditional_304
            self.before_conditional_304 = None
            if hook is not None:
                hook()
            response_etag = spec["response_304_etag"]
            if response_etag is None:
                response_etag = spec["etag"]
            headers = {} if response_etag is None else {"ETag": response_etag}
            response = _http_response(request, status, headers=headers)
        else:
            status = 200
            headers = {"Content-Type": "application/json"}
            if spec["etag"] is not None:
                headers["ETag"] = spec["etag"]
            # Content is what Requests exposes after any HTTP gzip decoding.
            if spec["payload"].get("syntheticHttpGzip"):
                headers["Content-Encoding"] = "gzip"
            response = _http_response(
                request, status, data=spec["payload"], headers=headers
            )

        self.data_status_counts.setdefault(status, 0)
        self.data_status_counts[status] += 1
        self.calls.append(
            {
                "kind": "data",
                "dataset": dataset,
                "identifier": identifier,
                "if_none_match": conditional,
                "request": request,
                "status": status,
            }
        )
        return response

    def close(self):
        pass

    @property
    def data_calls(self):
        return [call for call in self.calls if call["kind"] == "data"]


def _client(tmp_path, api, *, key=SYNTHETIC_KEY, base=SYNTHETIC_BASE, **kw):
    client = Client(key, cache_dir=tmp_path, base_url=base, **kw)
    parsed = urlsplit(client.base_url)
    client._transport._session.mount(
        f"{parsed.scheme}://{parsed.netloc}/", api
    )
    return client


@pytest.mark.parametrize(
    "dataset,method_name,model,payload",
    [
        (
            "shares-outstanding",
            "shares_outstanding",
            SharesOutstanding,
            _so_payload("AAPL", "AAPLRkNMQu0"),
        ),
        (
            "reference",
            "reference",
            Reference,
            _reference_payload("AAPL", "AAPLRkNMQu0"),
        ),
    ],
)
def test_first_and_unchanged_calls_are_200_then_304_without_manifest(
    tmp_path, dataset, method_name, model, payload
):
    api = SyntheticContractAPI()
    api.set_resource(dataset, "AAPL", payload, etag='W/"server-opaque"')
    client = _client(tmp_path, api)
    method = getattr(client, method_name)

    first = method(ticker="AAPL")
    second = method(ticker="AAPL")

    assert isinstance(first, model)
    assert isinstance(second, model)
    assert first.security_id == second.security_id == "AAPLRkNMQu0"
    assert [call["identifier"] for call in api.data_calls] == ["AAPL", "AAPL"]
    assert [call["status"] for call in api.data_calls] == [200, 304]
    assert api.data_calls[0]["if_none_match"] is None
    assert api.data_calls[1]["if_none_match"] == 'W/"server-opaque"'
    assert api.manifest_requests == 0
    assert client.diagnostics["cached_304_returns"] == 1


@pytest.mark.parametrize(
    "dataset,method_name",
    [
        ("shares-outstanding", "shares_outstanding"),
        ("reference", "reference"),
    ],
)
def test_required_fifty_security_two_pass_synthetic_counts(
    tmp_path, dataset, method_name
):
    api = SyntheticContractAPI()
    identifiers = [f"SYN{number:02d}" for number in range(50)]
    for number, identifier in enumerate(identifiers):
        security_id = f"{identifier}CaseId"
        payload = (
            _so_payload(identifier, security_id, shares=number + 1)
            if dataset == "shares-outstanding"
            else _reference_payload(identifier, security_id)
        )
        api.set_resource(dataset, identifier, payload, etag=f'"etag-{number}"')

    client = _client(tmp_path, api)
    method = getattr(client, method_name)
    for identifier in identifiers:
        assert method(identifier).security_id == f"{identifier}CaseId"
    for identifier in identifiers:
        assert method(identifier).security_id == f"{identifier}CaseId"

    # Synthetic contract measurements, not hosted quota observations.
    assert len(api.data_calls) == 100
    assert api.data_status_counts[200] == 50
    assert api.data_status_counts[304] == 50
    assert api.manifest_requests == 0
    assert client.diagnostics["conditional_requests"] == 50


def test_changed_payload_and_ticker_recycling_never_use_old_owner(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "shares-outstanding",
        "ABC",
        _so_payload("ABC", "security-A", shares=100),
        etag='"owner-a"',
    )
    client = _client(tmp_path, api)
    assert client.shares_outstanding("ABC").security_id == "security-A"

    # Same application timestamp, new owner and bytes: the server's 200 wins.
    api.set_resource(
        "shares-outstanding",
        "ABC",
        _so_payload("historical-label-B", "security-B", shares=200),
        etag='"owner-b"',
    )
    moved = client.shares_outstanding("ABC")
    assert moved.security_id == "security-B"
    assert moved.ticker == "historical-label-B"
    assert api.data_calls[-1]["identifier"] == "ABC"
    assert api.data_calls[-1]["if_none_match"] == '"owner-a"'

    api.set_resource(
        "shares-outstanding",
        "ABC",
        _so_payload("ABC", "security-B"),
        status=404,
    )
    with pytest.raises(DatawiserNotFoundError):
        client.shares_outstanding("ABC")
    assert api.data_calls[-1]["identifier"] == "ABC"
    assert len(api.data_calls) == 3


def test_profile_change_and_expiry_are_server_governed(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "reference",
        "AAPL",
        _reference_payload("AAPL", "sid", marker="basic-long"),
        etag='"basic-long"',
    )
    client = _client(tmp_path, api)
    assert client.reference("AAPL").raw["profile"] == "basic-long"

    api.set_resource(
        "reference",
        "AAPL",
        _reference_payload("AAPL", "sid", marker="free-short"),
        etag='"free-short"',
    )
    assert client.reference("AAPL").raw["profile"] == "free-short"

    api.set_resource(
        "reference",
        "AAPL",
        _reference_payload("AAPL", "sid"),
        status=403,
    )
    with pytest.raises(Exception) as caught:
        client.reference("AAPL")
    assert getattr(caught.value, "status_code", None) == 403


@pytest.mark.parametrize(
    "identifier",
    [
        "AAPL",
        "AAPL.json",
        "AAPL-US",
        "AAPL-US-AAPLRkNMQu0",
        "AAPL-AAPLRkNMQu0",
        "AAPLRkNMQu0",
        "AAPL-CA",
        "AAPL-CA-AAPLR3mJXUA",
        "GOOG",
        "GOOGL",
        "MiXeDCaseId",
    ],
)
def test_supported_identifier_forms_are_passed_through_exactly(tmp_path, identifier):
    api = SyntheticContractAPI()
    api.set_resource(
        "reference",
        identifier,
        _reference_payload(identifier, f"sid-{identifier}"),
        etag='"one"',
    )
    client = _client(tmp_path, api)
    client.reference(identifier)

    assert api.data_calls[0]["identifier"] == identifier
    assert api.data_calls[0]["request"].url.endswith(
        "/reference/" + identifier
    )


def test_alias_with_reserved_url_characters_is_one_encoded_segment(tmp_path):
    identifier = "MiXeD/alias ?#"
    api = SyntheticContractAPI()
    api.set_resource(
        "reference",
        identifier,
        _reference_payload(identifier, "MiXeDCaseId"),
        etag='"one"',
    )
    client = _client(tmp_path, api)
    assert client.reference(identifier).security_id == "MiXeDCaseId"
    assert api.data_calls[0]["request"].url.endswith(
        "/reference/MiXeD%2Falias%20%3F%23"
    )


def test_cache_candidates_are_isolated_by_key_origin_and_base_path(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "reference", "AAPL", _reference_payload("AAPL", "sid"), etag='"v1"'
    )

    first = _client(tmp_path, api, key="synthetic-key-a")
    first.reference("AAPL")
    same_namespace = _client(
        tmp_path, api, key="synthetic-key-a", base=SYNTHETIC_BASE + "/"
    )
    same_namespace.reference("AAPL")
    other_key = _client(tmp_path, api, key="synthetic-key-b")
    other_key.reference("AAPL")
    other_path = _client(
        tmp_path,
        api,
        key="synthetic-key-a",
        base="https://synthetic.datawiser.test/other/v1",
    )
    other_path.reference("AAPL")
    other_origin = _client(
        tmp_path,
        api,
        key="synthetic-key-a",
        base="https://other.synthetic.datawiser.test/v1",
    )
    other_origin.reference("AAPL")

    assert [call["if_none_match"] for call in api.data_calls] == [
        None,
        '"v1"',
        None,
        None,
        None,
    ]


def test_no_etag_means_future_calls_are_ordinary_gets(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "shares-outstanding",
        "AAPL",
        _so_payload("AAPL", "sid"),
        etag=None,
    )
    client = _client(tmp_path, api)
    client.shares_outstanding("AAPL")
    client.shares_outstanding("AAPL")

    assert [call["status"] for call in api.data_calls] == [200, 200]
    assert all(call["if_none_match"] is None for call in api.data_calls)
    assert client.diagnostics["responses_without_etag"] == 2


def test_disappearing_body_after_304_has_one_unconditional_recovery(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "shares-outstanding",
        "AAPL",
        _so_payload("AAPL", "sid"),
        etag='"v1"',
    )
    client = _client(tmp_path, api)
    client.shares_outstanding("AAPL")
    candidate = client._cache.get_data("shares-outstanding", "AAPL")
    assert candidate is not None
    api.before_conditional_304 = candidate.body_path.unlink

    recovered = client.shares_outstanding("AAPL")

    assert recovered.security_id == "sid"
    assert [call["status"] for call in api.data_calls] == [200, 304, 200]
    assert api.data_calls[-1]["if_none_match"] is None
    assert client.diagnostics["unconditional_recoveries"] == 1


def test_unsolicited_304_recovery_is_capped(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "reference",
        "AAPL",
        _reference_payload("AAPL", "sid"),
        etag='"v1"',
        always_304=True,
    )
    client = _client(tmp_path, api)

    with pytest.raises(DatawiserCacheError):
        client.reference("AAPL")
    assert len(api.data_calls) == 2
    assert all(call["if_none_match"] is None for call in api.data_calls)
    assert client.diagnostics["unconditional_recoveries"] == 1


def test_inconsistent_304_validator_recovers_once(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "reference",
        "AAPL",
        _reference_payload("AAPL", "sid"),
        etag='"v1"',
    )
    client = _client(tmp_path, api)
    client.reference("AAPL")
    api.resources[("reference", "AAPL")]["response_304_etag"] = '"other"'

    assert client.reference("AAPL").security_id == "sid"
    assert [call["status"] for call in api.data_calls] == [200, 304, 200]
    assert client.diagnostics["unconditional_recoveries"] == 1


def test_corrupt_body_is_discarded_before_validator_is_sent(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "reference",
        "AAPL",
        _reference_payload("AAPL", "sid"),
        etag='"v1"',
    )
    client = _client(tmp_path, api)
    client.reference("AAPL")
    candidate = client._cache.get_data("reference", "AAPL")
    assert candidate is not None
    candidate.body_path.write_bytes(b"corrupt")

    assert client.reference("AAPL").security_id == "sid"
    assert [call["if_none_match"] for call in api.data_calls] == [None, None]
    assert [call["status"] for call in api.data_calls] == [200, 200]


def test_plain_and_requests_decoded_gzip_payloads_share_normal_model_flow(tmp_path):
    api = SyntheticContractAPI()
    plain = _reference_payload("PLAIN", "plain-id")
    encoded = _so_payload("GZIP", "gzip-id")
    encoded["syntheticHttpGzip"] = True
    api.set_resource("reference", "PLAIN", plain, etag='"plain"')
    api.set_resource("shares-outstanding", "GZIP", encoded, etag='"gzip"')
    client = _client(tmp_path, api)

    assert client.reference("PLAIN").security_id == "plain-id"
    assert client.shares_outstanding("GZIP").security_id == "gzip-id"
    # Local cache gzip is independently readable and model-valid.
    assert client._cache.get_data("reference", "PLAIN").data == plain
    assert client._cache.get_data("shares-outstanding", "GZIP").data == encoded


def test_manifest_cache_refresh_dedup_missing_label_and_unknown_format(tmp_path):
    api = SyntheticContractAPI()
    entry = {
        "ticker": "AAPL",
        "security_id": "sid-aapl",
        "last_update": "2026-01-01",
    }
    api.manifests["reference"] = {
        "AAPL": dict(entry),
        "AAPL-US-sid-aapl": dict(entry),
        "sid-no-current-label": {
            "security_id": "sid-no-current-label",
            "last_update": "2026-01-01",
        },
    }
    client = _client(tmp_path, api, manifest_ttl=3600)

    first = client.universe("reference")
    second = client.universe("reference")
    refreshed = client.universe("reference", refresh=True)

    assert len(first.entries) == len(second.entries) == len(refreshed.entries) == 2
    assert first.tickers == ["AAPL"]
    assert "sid-no-current-label" in first
    assert api.manifest_requests == 2

    api.manifests["reference"] = {
        "format_version": {"unexpected_internal_shape": 3}
    }
    with pytest.raises(ManifestCompatibilityError):
        client.universe("reference", refresh=True)


def test_manifest_zero_ttl_and_malformed_update_fields(tmp_path):
    api = SyntheticContractAPI()
    api.manifests["free-float"] = {
        "SYN": {
            "ticker": "SYN",
            "security_id": "sid",
            "last_update": "2026-01-01",
            "export_stem": 123,
        }
    }
    client = _client(tmp_path, api, manifest_ttl=0)
    with pytest.raises(ManifestCompatibilityError):
        client.free_float("SYN")
    with pytest.raises(ManifestCompatibilityError):
        client.free_float("SYN")
    assert api.manifest_requests == 2
    assert api.data_calls == []


def test_ff_and_ffe_models_regress_without_claiming_conditional_support(tmp_path):
    api = SyntheticContractAPI()
    api.manifests["free-float"] = {
        "SYN": {
            "ticker": "SYN",
            "security_id": "ff-id",
            "export_stem": "SYN-ff-id",
            "last_update": "2026-01-01",
        },
        "SYN-ff-id": {
            "ticker": "SYN",
            "security_id": "ff-id",
            "export_stem": "SYN-ff-id",
            "last_update": "2026-01-01",
        },
    }
    api.manifests["free-float-events"] = {
        "SYN": {
            "ticker": "SYN",
            "security_id": "ffe-id",
            "export_stem": "SYN-ffe-id",
            "last_update": "2026-01-01",
        },
        "SYN-ffe-id": {
            "ticker": "SYN",
            "security_id": "ffe-id",
            "export_stem": "SYN-ffe-id",
            "last_update": "2026-01-01",
        },
    }
    api.set_resource(
        "free-float",
        "SYN-ff-id",
        _free_float_payload("SYN", "ff-id"),
        etag='"ignored-for-conditional"',
    )
    api.set_resource(
        "free-float-events",
        "SYN-ffe-id",
        _ffe_payload("SYN", "ffe-id"),
        etag='"ignored-for-conditional"',
    )
    client = _client(tmp_path, api)

    assert isinstance(client.free_float("SYN"), FreeFloat)
    assert isinstance(client.free_float("SYN"), FreeFloat)
    assert isinstance(client.free_float_events("SYN"), FreeFloatEvents)
    assert isinstance(
        client.free_float_events_detail("SYN"), FreeFloatEventsDetail
    )
    assert [call["identifier"] for call in api.data_calls] == [
        "SYN-ff-id",
        "SYN-ff-id",
        "SYN-ffe-id",
        "SYN-ffe-id",
    ]
    assert all(call["if_none_match"] is None for call in api.data_calls)
    assert api.data_status_counts[200] == 4
    assert api.data_status_counts[304] == 0


def test_invalid_200_is_not_cached_or_automatically_retried(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "shares-outstanding",
        "AAPL",
        {"ticker": "AAPL", "securityId": "sid", "events": [{}]},
        etag='"invalid"',
    )
    client = _client(tmp_path, api)

    with pytest.raises(DatawiserResponseError) as caught:
        client.shares_outstanding("AAPL")
    assert caught.value.possibly_charged is True
    assert len(api.data_calls) == 1
    assert client._cache.get_data("shares-outstanding", "AAPL") is None

    api.set_resource(
        "shares-outstanding",
        "AAPL",
        _so_payload("AAPL", "sid"),
        etag='"valid"',
    )
    assert client.shares_outstanding("AAPL").security_id == "sid"
    assert api.data_calls[-1]["if_none_match"] is None


def test_nested_reference_ids_and_false_values_survive_model_and_cache(tmp_path):
    payload = {
        "identifiers": {"tkr": "SYN"},
        "dwIds": {
            "securityId": "NestedCaseId",
            "companyId": "nested-company-id",
            "listingId": "nested-listing-id",
        },
        "enhanced": None,
        "isPrimary": False,
    }
    api = SyntheticContractAPI()
    api.set_resource("reference", "SYN", payload, etag='"nested"')
    client = _client(tmp_path, api)

    first = client.reference("SYN")
    second = client.reference("SYN")
    assert first.security_id == second.security_id == "NestedCaseId"
    assert first.is_primary is False
    assert first.company_id == "nested-company-id"
    assert first.listing_id == "nested-listing-id"
    assert api.data_status_counts == {200: 1, 304: 1}


def test_use_cache_false_and_refresh_cache_force_unconditional_requests(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "reference", "AAPL", _reference_payload("AAPL", "sid"), etag='"v1"'
    )
    cached_client = _client(tmp_path, api)
    cached_client.reference("AAPL")

    no_cache = _client(tmp_path, api, use_cache=False)
    no_cache.reference("AAPL")
    no_cache.reference("AAPL")
    refresh = _client(tmp_path, api, use_cache=False, refresh_cache=True)
    refresh.reference("AAPL")

    assert [call["if_none_match"] for call in api.data_calls] == [
        None,
        None,
        None,
        None,
    ]
    assert refresh._cache.get_data("reference", "AAPL") is not None


def test_client_never_sends_reserved_internal_headers(tmp_path):
    api = SyntheticContractAPI()
    api.set_resource(
        "reference", "AAPL", _reference_payload("AAPL", "sid"), etag='"v1"'
    )
    client = _client(tmp_path, api)
    client.reference("AAPL")
    client.reference("AAPL")

    for call in api.data_calls:
        lower_names = {name.lower() for name in call["request"].headers}
        assert "x-api-key" in lower_names
        assert "authorization" not in lower_names
        assert not any(name.startswith("x-internal-") for name in lower_names)
        assert not any(name.startswith("x-vercel-") for name in lower_names)
