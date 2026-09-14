from __future__ import annotations

import importlib.util
import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Optional

import pytest
import requests


SCRIPT_PATH = Path(__file__).parents[1] / "examples" / "d4_later_smoke.py"
SPEC = importlib.util.spec_from_file_location("d4_later_smoke", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
SMOKE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SMOKE
SPEC.loader.exec_module(SMOKE)


def _response(
    path: str,
    *,
    status: int = 200,
    body: bytes = b'{}',
    request_headers: Optional[dict[str, str]] = None,
    response_headers: Optional[dict[str, str]] = None,
) -> requests.Response:
    request = requests.Request(
        "GET",
        "https://datawiser-m7pbzbu6l-datawisers-projects.vercel.app" + path,
        headers=request_headers,
    ).prepare()
    response = requests.Response()
    response.status_code = status
    response.request = request
    response.url = request.url
    response._content = body
    response.headers.update(response_headers or {})
    return response


def test_key_file_parser_is_strict_and_does_not_evaluate(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    key_file = tmp_path / "client-d4-smoke.env"
    key_file.write_text("DATAWISER_API_KEY=synthetic-$HOME-value\n")
    key_file.chmod(0o600)

    assert SMOKE._read_key_file(key_file) == "synthetic-$HOME-value"

    key_file.write_text("DATAWISER_API_KEY=synthetic\nSECOND=value\n")
    with pytest.raises(SMOKE.SmokeFailure):
        SMOKE._read_key_file(key_file)


def test_key_file_parser_rejects_loose_permissions(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    key_file = tmp_path / "client-d4-smoke.env"
    key_file.write_text("DATAWISER_API_KEY=synthetic\n")
    key_file.chmod(0o644)

    with pytest.raises(SMOKE.SmokeFailure, match="0600"):
        SMOKE._read_key_file(key_file)


def test_response_observer_records_only_safe_request_facts() -> None:
    observer = SMOKE.ResponseObserver()
    response = _response(
        "/v1/shares-outstanding/AAPL",
        body=b'{"securityId":"synthetic"}',
        request_headers={
            "X-API-Key": "must-not-appear",
            "If-None-Match": '"opaque-validator"',
        },
        response_headers={
            "ETag": '"opaque-validator"',
            "Set-Cookie": "must-not-appear",
            "X-RateLimit-Remaining": "9",
        },
    )

    observer(response)

    safe = observer.records[0].safe_dict()
    encoded = json.dumps(safe)
    assert safe["path"] == "/v1/shares-outstanding/AAPL"
    assert safe["validator_present"] is True
    assert safe["response_etag_present"] is True
    assert safe["quota_headers"] == {"X-RateLimit-Remaining": "9"}
    assert "must-not-appear" not in encoded
    assert "opaque-validator" not in encoded


def test_response_observer_rejects_a_different_origin() -> None:
    observer = SMOKE.ResponseObserver()
    response = _response("/v1/reference/AAPL")
    response.request.url = "https://api.datawiser.ai/v1/reference/AAPL"

    with pytest.raises(SMOKE.SmokeFailure, match="approved Preview"):
        observer(response)


def test_manifest_summary_reports_fields_without_financial_values() -> None:
    summary = SMOKE._manifest_field_summary(
        {
            "AAPL": {
                "security_id": SMOKE.EXPECTED_SECURITY_ID,
                "ticker": "AAPL",
                "country": "US",
                "qualified_key": "AAPL-US",
            }
        }
    )

    encoded = json.dumps(summary)
    assert summary["expected_security_id_value_fields"] == ["security_id"]
    assert summary["aapl_value_fields"] == ["ticker"]
    assert summary["candidate_fields"]["country"]["populated"] == 1
    assert SMOKE.EXPECTED_SECURITY_ID not in encoded
    assert "AAPL-US" not in encoded


def test_private_write_uses_owner_only_mode_and_no_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "fixture.json"
    SMOKE._private_write(output, b"{}")

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        SMOKE._private_write(output, b"changed")
    assert output.read_bytes() == b"{}"


class _ScriptedAdapter(requests.adapters.BaseAdapter):
    def __init__(self, script: list[dict[str, object]]) -> None:
        self.script = list(script)

    def send(self, request, **_kwargs):
        assert self.script, "unexpected request"
        expected = self.script.pop(0)
        assert request.path_url == expected["path"]
        assert ("If-None-Match" in request.headers) is expected["conditional"]

        response = requests.Response()
        response.status_code = int(expected["status"])
        response.request = request
        response.url = request.url
        response._content = expected["body"]
        response.headers.update(expected.get("headers", {}))
        return response

    def close(self) -> None:
        pass


def _encoded(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _so_body() -> bytes:
    event = {
        "asOf": "2026-01-01",
        "shareType": "common",
        "shares": 100,
        "source": "synthetic",
        "secType": "ordinary",
        "lastUpdate": "2026-01-02T00:00:00Z",
    }
    return _encoded(
        {
            "ticker": "AAPL",
            "securityId": SMOKE.EXPECTED_SECURITY_ID,
            "events": [event for _ in range(SMOKE.EXPECTED_SO_EVENTS)],
        }
    )


def _reference_body() -> bytes:
    return _encoded(
        {
            "ticker": "AAPL",
            "securityId": SMOKE.EXPECTED_SECURITY_ID,
            "companyName": "Synthetic Company",
        }
    )


def _normal_script(so_body: bytes, reference_body: bytes):
    so_manifest = _encoded(
        {
            "AAPL": {
                "security_id": SMOKE.EXPECTED_SECURITY_ID,
                "ticker": "AAPL",
                "country": "US",
                "qualified_key": "AAPL-US",
            }
        }
    )
    reference_manifest = _encoded(
        {
            "AAPL": {
                "securityId": SMOKE.EXPECTED_SECURITY_ID,
                "ticker": "AAPL",
            }
        }
    )
    return [
        {
            "path": "/v1/shares-outstanding/AAPL",
            "conditional": False,
            "status": 200,
            "body": so_body,
            "headers": {"ETag": '"so"', "Content-Type": "application/json"},
        },
        {
            "path": "/v1/shares-outstanding/AAPL",
            "conditional": True,
            "status": 304,
            "body": b"",
            "headers": {"ETag": '"so"'},
        },
        {
            "path": "/v1/reference/AAPL",
            "conditional": False,
            "status": 200,
            "body": reference_body,
            "headers": {"ETag": '"reference"'},
        },
        {
            "path": "/v1/reference/AAPL",
            "conditional": True,
            "status": 304,
            "body": b"",
            "headers": {"ETag": '"reference"'},
        },
        {
            "path": "/v1/reference/AAPL-CA",
            "conditional": False,
            "status": 404,
            "body": b'{"error":"not-found"}',
        },
        {
            "path": "/v1/manifest/shares-outstanding",
            "conditional": False,
            "status": 200,
            "body": so_manifest,
            "headers": {"Content-Type": "application/json"},
        },
        {
            "path": "/v1/manifest/reference",
            "conditional": False,
            "status": 200,
            "body": reference_manifest,
            "headers": {"Content-Type": "application/json"},
        },
    ]


def _contract_client(tmp_path: Path, adapter: _ScriptedAdapter):
    client = SMOKE.Client(
        "synthetic-api-key",
        cache_dir=tmp_path / "cache",
        base_url=SMOKE.APPROVED_PREVIEW_BASE_URL,
        timeout=30,
    )
    client._transport._session.mount("https://", adapter)
    observer = SMOKE.ResponseObserver()
    client._transport._session.hooks.setdefault("response", []).append(observer)
    return client, observer


def test_execute_runs_the_exact_seven_request_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    so_body = _so_body()
    reference_body = _reference_body()
    monkeypatch.setattr(
        SMOKE, "EXPECTED_SO_SHA256", hashlib.sha256(so_body).hexdigest()
    )
    monkeypatch.setattr(
        SMOKE,
        "EXPECTED_REFERENCE_SHA256",
        hashlib.sha256(reference_body).hexdigest(),
    )
    adapter = _ScriptedAdapter(_normal_script(so_body, reference_body))
    client, observer = _contract_client(tmp_path, adapter)

    try:
        result = SMOKE._execute(client, observer, run_dir)
    finally:
        client.close()

    assert not adapter.script
    assert result["status"] == "passed"
    assert result["counts"] == {
        "total_requests": 7,
        "data_200_bodies": 2,
        "not_modified_304": 2,
        "metadata_calls": 2,
        "denials": 1,
        "recovery_calls": 0,
        "decoded_bytes": observer.decoded_bytes,
    }
    assert [row["status"] for row in result["request_rows"]] == [
        200,
        304,
        200,
        304,
        404,
        200,
        200,
    ]
    assert [row["cached_model_returned"] for row in result["request_rows"]] == [
        False,
        True,
        False,
        True,
        False,
        False,
        False,
    ]
    assert result["diagnostics"]["manifest_requests"] == 0
    for paths in result["private_fixture_paths"].values():
        for path in paths.values():
            assert stat.S_IMODE(Path(path).stat().st_mode) == 0o600


def test_execute_stops_after_one_unusable_304_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    so_body = _so_body()
    monkeypatch.setattr(
        SMOKE, "EXPECTED_SO_SHA256", hashlib.sha256(so_body).hexdigest()
    )
    script = _normal_script(so_body, _reference_body())[:2]
    script.append(
        {
            "path": "/v1/shares-outstanding/AAPL",
            "conditional": False,
            "status": 200,
            "body": so_body,
            "headers": {"ETag": '"so-recovery"'},
        }
    )
    adapter = _ScriptedAdapter(script)
    client, observer = _contract_client(tmp_path, adapter)
    monkeypatch.setattr(client._cache, "reload_data", lambda _candidate: None)

    try:
        with pytest.raises(SMOKE.SmokeFailure, match="unexpected extra request"):
            SMOKE._execute(client, observer, run_dir)
    finally:
        client.close()

    assert not adapter.script
    assert [record.status for record in observer.records] == [200, 304, 200]
    assert observer.records[1].cached_model_returned is False
    assert observer.records[2].cached_model_returned is False
    assert observer.records[2].extra_request is True
    assert not (run_dir / "manifest-fixtures").exists()


def test_response_observer_enforces_decoded_material_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SMOKE, "MAX_DECODED_BYTES", 3)
    observer = SMOKE.ResponseObserver()

    with pytest.raises(SMOKE.SmokeFailure, match="25 MiB"):
        observer(_response("/v1/reference/AAPL", body=b"four"))
