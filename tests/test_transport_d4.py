"""Offline transport tests for the synthetic D4-L HTTP contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import pytest
import requests
from requests import PreparedRequest, Response
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from datawiserai import (
    DatawiserAPIError,
    DatawiserAccessError,
    DatawiserAuthenticationError,
    DatawiserNetworkError,
    DatawiserNotFoundError,
    DatawiserRateLimitError,
    DatawiserResponseError,
    DatawiserServiceError,
)
from datawiserai._transport import (
    Transport,
    canonicalize_base_url,
    is_valid_etag,
)


def _response(
    request: PreparedRequest,
    status: int,
    *,
    data=None,
    content: Optional[bytes] = None,
    headers=None,
) -> Response:
    result = Response()
    result.status_code = status
    result.request = request
    result.url = request.url
    result.headers = CaseInsensitiveDict(headers or {})
    if content is None:
        content = b"" if data is None else json.dumps(data).encode("utf-8")
    # A custom adapter receives already-decoded response bytes here, matching
    # what Requests exposes after HTTP gzip/deflate content decoding.
    result._content = content
    result.encoding = "utf-8"
    return result


class SyntheticAdapter(BaseAdapter):
    """No-socket Requests adapter backed by one deterministic callback."""

    def __init__(self, callback):
        self.callback = callback
        self.requests: list[PreparedRequest] = []
        self.send_kwargs: list[dict] = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        self.send_kwargs.append(kwargs)
        return self.callback(request, len(self.requests))

    def close(self):
        pass


def _mounted_transport(callback, **kwargs):
    transport = Transport(
        "pk_live_synthetic_transport_key",
        base_url="https://synthetic.example/v1",
        **kwargs,
    )
    adapter = SyntheticAdapter(callback)
    transport._session.mount("https://synthetic.example/", adapter)
    return transport, adapter


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://EXAMPLE.com:443/v1/", "https://example.com/v1"),
        ("http://localhost:8080/v1/", "http://localhost:8080/v1"),
        ("http://127.0.0.1:8000/a/v1", "http://127.0.0.1:8000/a/v1"),
        ("http://[::1]:8000/v1", "http://[::1]:8000/v1"),
    ],
)
def test_base_url_canonicalization_preserves_base_path(url, expected):
    assert canonicalize_base_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example/v1",
        "https://user:secret@api.example/v1",
        "https://api.example/v1?api_key=secret",
        "https://api.example/v1#fragment",
        "file:///tmp/api",
        "//api.example/v1",
    ],
)
def test_unsafe_base_urls_are_rejected_before_a_session_is_used(url):
    with pytest.raises(ValueError):
        Transport("pk_live_synthetic", base_url=url)


@pytest.mark.parametrize(
    "value,valid",
    [
        ('"opaque"', True),
        ('W/"opaque"', True),
        ('"one", "two"', False),
        ("*", False),
        ("opaque", False),
        ('"bad\r\nheader"', False),
        (None, False),
    ],
)
def test_only_one_safe_server_entity_tag_is_usable(value, valid):
    assert is_valid_etag(value) is valid


def test_resource_path_preserves_case_and_encodes_one_segment():
    def callback(request, _count):
        return _response(
            request,
            200,
            data={"securityId": "MiXeD-id"},
            headers={"ETag": 'W/"opaque"'},
        )

    transport, adapter = _mounted_transport(callback, timeout=(2, 7))
    result = transport.get_resource("shares-outstanding", "MiXeD/id ?#.json")

    assert result.etag == 'W/"opaque"'
    assert adapter.requests[0].url.endswith(
        "/shares-outstanding/MiXeD%2Fid%20%3F%23.json"
    )
    assert adapter.requests[0].headers["X-API-Key"] == (
        "pk_live_synthetic_transport_key"
    )
    assert adapter.requests[0].headers["Accept"] == "application/json"
    assert "gzip" in adapter.requests[0].headers["Accept-Encoding"]
    assert "If-None-Match" not in adapter.requests[0].headers
    assert adapter.send_kwargs[0]["timeout"] == (2.0, 7.0)


def test_conditional_304_is_not_parsed_as_json():
    def callback(request, _count):
        assert request.headers["If-None-Match"] == '"v1"'
        return _response(request, 304, headers={"ETag": '"v1"'})

    transport, adapter = _mounted_transport(callback)
    result = transport.get_resource("reference", "AAPL", etag='"v1"')

    assert result.not_modified
    assert result.data is None
    assert len(adapter.requests) == 1


def test_requests_decoded_gzip_json_is_decoded_exactly_once():
    payload = {"ticker": "AAPL", "securityId": "AAPLRkNMQu0"}

    def callback(request, _count):
        return _response(
            request,
            200,
            data=payload,
            headers={"Content-Encoding": "gzip", "ETag": '"gzip"'},
        )

    transport, _adapter = _mounted_transport(callback)
    assert transport.get_resource("reference", "AAPL").data == payload


@pytest.mark.parametrize(
    "status,error_type",
    [
        (401, DatawiserAuthenticationError),
        (403, DatawiserAccessError),
        (404, DatawiserNotFoundError),
        (429, DatawiserRateLimitError),
        (500, DatawiserServiceError),
        (503, DatawiserServiceError),
    ],
)
def test_statuses_have_typed_legacy_compatible_errors(status, error_type):
    def callback(request, _count):
        return _response(
            request,
            status,
            content=b'{"detail":"pk_live_must_not_be_rendered"}',
            headers={"Retry-After": "9999999", "X-RateLimit-Reset": "9999999"},
        )

    transport, adapter = _mounted_transport(callback)
    with pytest.raises(error_type) as caught:
        transport.get_resource("reference", "AAPL")

    assert isinstance(caught.value, DatawiserAPIError)
    assert caught.value.status_code == status
    assert "pk_live_must_not_be_rendered" not in str(caught.value)
    assert len(adapter.requests) == 1
    if status == 429:
        assert caught.value.retry_after == 86_400
        assert caught.value.rate_limit_reset is not None


def test_redirect_is_not_followed_and_location_is_not_rendered():
    target = "https://storage.example/private?token=do-not-render"

    def callback(request, _count):
        return _response(request, 302, headers={"Location": target})

    transport, adapter = _mounted_transport(callback)
    with pytest.raises(DatawiserAPIError) as caught:
        transport.get_resource("reference", "AAPL")

    assert caught.value.status_code == 302
    assert target not in str(caught.value)
    assert len(adapter.requests) == 1


def test_network_and_malformed_json_are_not_retried():
    def timeout_callback(_request, _count):
        raise requests.Timeout("proxy https://user:secret@example.invalid")

    transport, adapter = _mounted_transport(timeout_callback)
    with pytest.raises(DatawiserNetworkError) as caught:
        transport.get_resource("shares-outstanding", "AAPL")
    assert caught.value.possibly_charged is True
    assert "secret" not in str(caught.value)
    assert len(adapter.requests) == 1

    def malformed_callback(request, _count):
        return _response(request, 200, content=b"not-json")

    transport, adapter = _mounted_transport(malformed_callback)
    with pytest.raises(DatawiserResponseError) as caught:
        transport.get_resource("shares-outstanding", "AAPL")
    assert caught.value.possibly_charged is True
    assert len(adapter.requests) == 1


def test_malformed_response_etag_and_nonempty_304_are_rejected_without_retry():
    def bad_etag(request, _count):
        return _response(
            request, 200, data={"ok": True}, headers={"ETag": "bad\nvalue"}
        )

    transport, adapter = _mounted_transport(bad_etag)
    with pytest.raises(DatawiserResponseError):
        transport.get_resource("reference", "AAPL")
    assert len(adapter.requests) == 1

    def bad_304(request, _count):
        return _response(request, 304, content=b"unexpected")

    transport, adapter = _mounted_transport(bad_304)
    with pytest.raises(DatawiserResponseError):
        transport.get_resource("reference", "AAPL", etag='"v1"')
    assert len(adapter.requests) == 1


def test_invalid_local_validator_is_never_transmitted():
    def callback(request, _count):
        assert "If-None-Match" not in request.headers
        return _response(request, 200, data={"ok": True})

    transport, _adapter = _mounted_transport(callback)
    transport.get_resource("reference", "AAPL", etag='"x"\r\nX-Evil: yes')
