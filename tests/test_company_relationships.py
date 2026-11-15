"""Company identity, decimal decoding, discovery and authenticated cache reuse."""
from decimal import Decimal
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import pytest
from requests import Response
from requests.adapters import BaseAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from datawiserai import Client, CompanyUniverse, DatawiserAccessError, DatawiserResponseError


def payload():
    return {"schemaVersion": "company-relationships-v1", "company": {"companyId": "Private2", "name": "Private supplier"},
        "selection": {"kind": "latest_disclosed", "knowledgeTimeMode": "current_source_current_knowledge",
                      "resolvedOnly": True, "includeUnspecified": False, "statuses": []},
        "relationships": [{"relationshipId": "relation1", "reportingCompany": {"companyId": "Reporter1", "name": "Reporter"},
            "counterparty": {"companyId": "Private2", "name": "Private supplier"},
            "relationshipType": "supplier", "viewRelationshipType": "customer", "directness": "direct", "status": "current",
            "filingDate": "2026-11-01", "source": {"documentId": "document1", "url": "https://www.sec.gov/example"},
            "evidence": [{"evidenceId": "evidence1", "text": "Reporter buys from Private supplier."}],
            "metrics": [{"metricId": "metric1", "evidenceId": "evidence1", "metricType": "purchase_share",
                "value": "0.1234567890", "rawValue": "approximately 12.34567890%", "valueQualifier": "approx",
                "basisParty": "reporting_company", "basisCompanyId": "Reporter1", "basisDescription": "purchases"}]}]}


class CompanyAPI(BaseAdapter):
    def __init__(self):
        self.body, self.allowed, self.calls = payload(), True, []
    def send(self, request, **kwargs):
        self.calls.append(request)
        response = Response()
        response.request, response.url, response.encoding = request, request.url, "utf-8"
        path = urlsplit(request.url).path
        response.status_code = 200
        if not self.allowed:
            response.status_code, body = 403, {"detail": "access revoked"}
        elif path.endswith("/updates"):
            body = {"Private2": {"company_id": "Private2", "company_name": "Private supplier",
                                 "last_update": "2026-11-13T12:00:00Z", "etag": "company-v1"}}
        elif path.endswith("/Private2"):
            body = self.body
            response.headers["ETag"] = '"company-v1"'
            if request.headers.get("If-None-Match") == '"company-v1"':
                response.status_code = 304
        else:
            response.status_code, body = 404, {"detail": "company missing"}
        response._content = b"" if response.status_code == 304 else json.dumps(body).encode()
        return response
    def close(self):
        pass


def client_api(tmp_path):
    client = Client("synthetic-company-key", base_url="https://companies.test/v1", cache_dir=tmp_path)
    api = CompanyAPI()
    client._transport._session.mount("https://companies.test/", api)
    return client, api


def test_real_transport_company_cache_and_decimal_precision(tmp_path):
    client, api = client_api(tmp_path)
    with client:
        first = client.company_relationships("Private2")
        second = client.company_relationships("Private2")
        assert first == second and first.company_id == "Private2"
        row = first.relationships[0]
        assert row.view_relationship_type == "customer" and row.reporting_company.company_id == "Reporter1"
        assert row.metrics[0].value == Decimal("0.1234567890")
        assert row.metrics[0].basis_company_id == "Reporter1"
        assert row.evidence[0].evidence_id == row.metrics[0].evidence_id
        assert api.calls[1].headers["If-None-Match"] == '"company-v1"'
        assert client.diagnostics["cached_304_returns"] == 1
        assert client._cache.get_data("company-relationships", "Private2").security_id == "Private2"
        universe = client.universe("company_relationships")
        assert isinstance(universe, CompanyUniverse) and universe.company_ids == ["Private2"]
        assert "Private2" in universe and "private2" not in universe
        api.allowed = False
        with pytest.raises(DatawiserAccessError):
            client.company_relationships("Private2")
        assert len(api.calls) == 4  # cached data never bypasses server access


@pytest.mark.parametrize("problem", ["company", "perspective", "basis", "evidence", "numeric", "schema"])
def test_invalid_company_payload_is_not_cached(tmp_path, problem):
    client, api = client_api(tmp_path)
    row = api.body["relationships"][0]
    if problem == "company":
        api.body = {**payload(), "company": {"companyId": "Wrong", "name": "Wrong"}, "relationships": []}
    if problem == "perspective": row["viewRelationshipType"] = "supplier"
    if problem == "basis": row["metrics"][0]["basisCompanyId"] = "Private2"
    if problem == "evidence": row["metrics"][0]["evidenceId"] = "another"
    if problem == "numeric": row["metrics"][0]["value"] = 0.12
    if problem == "schema": api.body["schemaVersion"] = "unknown-v2"
    with client, pytest.raises(DatawiserResponseError):
        client.company_relationships("Private2")
    assert client._cache.get_data("company-relationships", "Private2") is None


def test_company_payload_cannot_enter_a_security_dataset_cache(tmp_path):
    client, api = client_api(tmp_path)
    with client:
        with pytest.raises(ValueError):
            client._cache.put_data("reference", "Private2", "Private2", payload())
