"""Offline tests for the versioned D4-L cache implementation."""

from __future__ import annotations

import gzip
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from datawiserai._cache import FileCache


SYNTHETIC_KEY = "pk_live_synthetic_cache_namespace_secret"


def _cache(tmp_path, *, base="https://api.example/v1", key=SYNTHETIC_KEY, **kw):
    return FileCache(tmp_path, api_base=base, api_key=key, **kw)


def _so(security_id, shares=100):
    return {
        "ticker": "SYN",
        "securityId": security_id,
        "events": [
            {
                "asOf": "2026-01-01",
                "shareType": "issued",
                "shares": shares,
                "source": "synthetic-contract-fixture",
                "secType": "Common Stock",
                "lastUpdate": "same-timestamp",
            }
        ],
    }


def test_origin_base_path_key_and_model_have_distinct_namespaces(tmp_path):
    caches = [
        _cache(tmp_path, base="https://api.example/v1", key="key-a"),
        _cache(tmp_path, base="https://api.example/other/v1", key="key-a"),
        _cache(tmp_path, base="https://other.example/v1", key="key-a"),
        _cache(tmp_path, base="https://api.example/v1", key="key-b"),
        _cache(
            tmp_path,
            base="https://api.example/v1",
            key="key-a",
            model_version="2",
        ),
    ]

    assert len({cache.namespace_dir for cache in caches}) == len(caches)


def test_alias_security_id_etag_and_payload_are_integrity_checked(tmp_path):
    cache = _cache(tmp_path)
    payload = _so("SecurityCaseSensitive")
    written = cache.put_data(
        "shares-outstanding",
        "SYN-US",
        "SecurityCaseSensitive",
        payload,
        etag='W/"opaque-etag"',
    )

    candidate = cache.get_data("shares-outstanding", "SYN-US")
    assert candidate is not None
    assert candidate.security_id == "SecurityCaseSensitive"
    assert candidate.etag == 'W/"opaque-etag"'
    assert candidate.data == payload
    assert cache.reload_data(written) == payload

    written.body_path.write_bytes(b"truncated gzip")
    assert cache.reload_data(written) is None
    assert cache.get_data("shares-outstanding", "SYN-US") is None


def test_nested_reference_security_id_is_supported(tmp_path):
    cache = _cache(tmp_path)
    payload = {
        "identifiers": {"tkr": "SYN"},
        "dwIds": {"securityId": "NestedCaseId"},
        "companyId": "company",
        "listingId": "listing",
    }

    cache.put_data(
        "reference", "SYN", "NestedCaseId", payload, etag='"ref"'
    )
    candidate = cache.get_data("reference", "SYN")
    assert candidate is not None
    assert candidate.data == payload


def test_invalid_cached_validator_is_discarded_not_returned(tmp_path):
    cache = _cache(tmp_path)
    cache.put_data(
        "shares-outstanding", "SYN", "sid", _so("sid"), etag='"safe"'
    )
    alias_path = next((cache.namespace_dir / "aliases").rglob("*.json.gz"))
    with gzip.open(alias_path, "rt", encoding="utf-8") as handle:
        alias = json.load(handle)
    alias["etag"] = '"safe"\r\nX-Evil: injected'
    with gzip.open(alias_path, "wt", encoding="utf-8") as handle:
        json.dump(alias, handle)

    assert cache.get_data("shares-outstanding", "SYN") is None
    assert not alias_path.exists()


def test_raw_key_and_path_traversal_inputs_never_become_path_components(tmp_path):
    cache = _cache(tmp_path)
    cache.put_data(
        "../../dataset",
        "../alias/with/slashes",
        "../Case/Security",
        _so("../Case/Security"),
        etag='"etag"',
    )

    relative_names = [
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")
    ]
    joined_names = "\n".join(relative_names)
    assert SYNTHETIC_KEY not in joined_names
    assert "alias/with" not in joined_names
    assert "Case/Security" not in joined_names
    assert cache.get_data("../../dataset", "../alias/with/slashes") is not None

    for path in cache.namespace_dir.rglob("*.json.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            assert SYNTHETIC_KEY not in handle.read()


def test_legacy_files_are_never_read_migrated_or_cleared(tmp_path):
    legacy = tmp_path / "shares-outstanding" / "SYN.json.gz"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy-timestamp-cache")

    cache = _cache(tmp_path)
    assert cache.get_data("shares-outstanding", "SYN") is None
    cache.put_data(
        "shares-outstanding", "SYN", "sid", _so("sid"), etag='"v2"'
    )
    assert cache.clear() >= 2
    assert legacy.read_bytes() == b"legacy-timestamp-cache"


def test_manifest_ttl_and_integrity(tmp_path, monkeypatch):
    cache = _cache(tmp_path)
    manifest = {
        "SYN": {
            "ticker": "SYN",
            "security_id": "sid",
            "last_update": "2026-01-01",
        }
    }
    cache.put_manifest("reference", manifest, fetched_at=100.0)
    monkeypatch.setattr("datawiserai._cache.time.time", lambda: 104.0)
    assert cache.get_manifest("reference", max_age_seconds=5) == manifest
    assert cache.get_manifest("reference", max_age_seconds=3) is None


def test_implausibly_future_manifest_timestamp_is_discarded(tmp_path, monkeypatch):
    cache = _cache(tmp_path)
    manifest = {
        "SYN": {
            "ticker": "SYN",
            "security_id": "sid",
            "last_update": "2026-01-01",
        }
    }
    cache.put_manifest("reference", manifest, fetched_at=10_000.0)
    monkeypatch.setattr("datawiserai._cache.time.time", lambda: 100.0)

    assert cache.get_manifest("reference", max_age_seconds=60) is None


def test_concurrent_alias_replacement_is_always_complete(tmp_path):
    cache = _cache(tmp_path, max_entries=100, max_bytes=10 * 1024 * 1024)

    def write(version):
        payload = _so(f"sid-{version}", shares=version)
        cache.put_data(
            "shares-outstanding",
            "SYN",
            f"sid-{version}",
            payload,
            etag=f'"v{version}"',
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(1, 33)))

    candidate = cache.get_data("shares-outstanding", "SYN")
    assert candidate is not None
    version = int(candidate.etag.strip('"v'))
    assert candidate.security_id == f"sid-{version}"
    assert candidate.data["events"][0]["shares"] == version
    assert cache.reload_data(candidate) == candidate.data


def test_retention_bounds_and_owner_only_modes(tmp_path):
    cache = _cache(tmp_path, max_entries=1, max_bytes=10 * 1024 * 1024)
    for number in range(3):
        cache.put_data(
            "shares-outstanding",
            f"SYN-{number}",
            f"sid-{number}",
            _so(f"sid-{number}"),
            etag=f'"v{number}"',
        )

    aliases = list((cache.namespace_dir / "aliases").rglob("*.json.gz"))
    bodies = list((cache.namespace_dir / "data").rglob("*.json.gz"))
    assert len(aliases) <= 1
    assert len(bodies) <= 1
    if os.name == "posix":
        assert cache.namespace_dir.stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in aliases + bodies)


def test_zero_size_policy_evicts_current_v2_entry(tmp_path):
    cache = _cache(tmp_path, max_entries=100, max_bytes=0)
    cache.put_data(
        "shares-outstanding", "SYN", "sid", _so("sid"), etag='"v"'
    )
    assert cache.get_data("shares-outstanding", "SYN") is None
