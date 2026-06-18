from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from datawiserai import AmbiguousTickerError, Client


class FakeTransport:
    def __init__(self) -> None:
        self.data = {"ticker": "ABC", "securityId": "ABCsec", "events": []}
        self.get_calls = 0

    def get_manifest(self, endpoint: str):
        return {"ABC": {"last_update": "remote-1"}}

    def get(self, endpoint: str, ticker: str):
        self.get_calls += 1
        return self.data


def _cache_entry(cache_dir: Path, endpoint: str, ticker: str):
    with gzip.open(cache_dir / endpoint / f"{ticker}.json.gz", "rt", encoding="utf-8") as handle:
        return json.load(handle)


def test_cache_hit_uses_matching_cached_response():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        transport = FakeTransport()
        client = Client("test-key", cache_dir=cache_dir)
        client._transport = transport

        first = client._fetch("free-float-events", "ABC")
        transport.data = {"ticker": "ABC", "securityId": "changed", "events": []}
        second = client._fetch("free-float-events", "ABC")

        assert first["securityId"] == "ABCsec"
        assert second["securityId"] == "ABCsec"
        assert transport.get_calls == 1


def test_refresh_cache_bypasses_read_and_overwrites_cache():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        transport = FakeTransport()
        client = Client("test-key", cache_dir=cache_dir)
        client._transport = transport
        client._fetch("free-float-events", "ABC")

        transport.data = {"ticker": "ABC", "securityId": "fresh", "events": []}
        refresh_client = Client("test-key", cache_dir=cache_dir, refresh_cache=True)
        refresh_client._transport = transport
        refreshed = refresh_client._fetch("free-float-events", "ABC")
        cached = _cache_entry(cache_dir, "free-float-events", "ABC")

        assert refreshed["securityId"] == "fresh"
        assert cached["data"]["securityId"] == "fresh"
        assert transport.get_calls == 2


def test_corrupt_cache_file_is_treated_as_miss_and_rewritten():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        cache_path = cache_dir / "free-float-events" / "ABC.json.gz"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(b"\x1f\x8b")

        transport = FakeTransport()
        client = Client("test-key", cache_dir=cache_dir)
        client._transport = transport
        data = client._fetch("free-float-events", "ABC")
        cached = _cache_entry(cache_dir, "free-float-events", "ABC")

        assert data["securityId"] == "ABCsec"
        assert cached["data"]["securityId"] == "ABCsec"
        assert transport.get_calls == 1


class ManifestTransport:
    def __init__(self, manifest):
        self.manifest = manifest
        self.get_calls = []

    def get_manifest(self, endpoint: str):
        return self.manifest

    def get(self, endpoint: str, ticker: str):
        self.get_calls.append((endpoint, ticker))
        return {"ticker": ticker, "securityId": ticker, "events": []}


def test_fetch_resolves_manifest_entry_by_ticker_field_when_key_is_not_ticker():
    with TemporaryDirectory() as tmp:
        manifest = {
            "META-METAJYotTYL": {
                "ticker": "META",
                "security_id": "METAJYotTYL",
                "export_stem": "META-METAJYotTYL",
                "last_update": "remote-1",
            }
        }
        transport = ManifestTransport(manifest)
        client = Client("test-key", cache_dir=Path(tmp))
        client._transport = transport

        data = client._fetch("free-float", "META")

        assert data["ticker"] == "META-METAJYotTYL"
        assert transport.get_calls == [("free-float", "META-METAJYotTYL")]


def test_fetch_defaults_to_active_security_when_ticker_has_delisted_match():
    with TemporaryDirectory() as tmp:
        manifest = {
            "META-old": {
                "ticker": "META",
                "security_id": "META-old",
                "export_stem": "META-old",
                "is_delisted": True,
                "last_update": "remote-old",
            },
            "META-new": {
                "ticker": "META",
                "security_id": "META-new",
                "export_stem": "META-new",
                "last_update": "remote-new",
            },
        }
        transport = ManifestTransport(manifest)
        client = Client("test-key", cache_dir=Path(tmp))
        client._transport = transport

        resolved_ticker, entry = client._resolve_manifest_entry(
            "free-float", manifest, "META"
        )
        data = client._fetch("free-float", "META")

        assert resolved_ticker == "META-new"
        assert entry["security_id"] == "META-new"
        assert data["ticker"] == "META-new"
        assert transport.get_calls == [("free-float", "META-new")]


def test_fetch_ignores_full_qc_entries_when_resolving_ticker_default():
    with TemporaryDirectory() as tmp:
        manifest = {
            "META-META-new": {
                "ticker": "META",
                "security_id": "META-new",
                "export_file": "META-META-new.json.gz",
                "export_stem": "META-META-new",
                "last_update": "remote-new",
            },
            "META-META-new-full": {
                "ticker": "META",
                "security_id": "META-new",
                "export_file": "META-META-new-full.json.gz",
                "export_stem": "META-META-new-full",
                "last_update": "remote-new",
            },
        }
        transport = ManifestTransport(manifest)
        client = Client("test-key", cache_dir=Path(tmp))
        client._transport = transport

        resolved_ticker, entry = client._resolve_manifest_entry(
            "free-float-events", manifest, "META"
        )
        data = client._fetch("free-float-events", "META")

        assert resolved_ticker == "META-META-new"
        assert entry["export_file"] == "META-META-new.json.gz"
        assert data["ticker"] == "META-META-new"
        assert transport.get_calls == [("free-float-events", "META-META-new")]


def test_fetch_allows_exact_security_id_for_delisted_security():
    with TemporaryDirectory() as tmp:
        manifest = {
            "META-old": {
                "ticker": "META",
                "security_id": "META-old",
                "is_delisted": True,
                "last_update": "remote-old",
            },
            "META-new": {
                "ticker": "META",
                "security_id": "META-new",
                "last_update": "remote-new",
            },
        }
        transport = ManifestTransport(manifest)
        client = Client("test-key", cache_dir=Path(tmp))
        client._transport = transport

        data = client._fetch("free-float", "META-old")

        assert data["ticker"] == "META-old"
        assert transport.get_calls == [("free-float", "META-old")]


def test_fetch_canonicalizes_exact_ticker_alias_to_export_stem():
    with TemporaryDirectory() as tmp:
        manifest = {
            "META": {
                "ticker": "META",
                "security_id": "META-new",
                "export_stem": "META-META-new",
                "last_update": "remote-new",
            },
            "META-new": {
                "ticker": "META",
                "security_id": "META-new",
                "export_stem": "META-META-new",
                "last_update": "remote-new",
            },
            "META-META-new": {
                "ticker": "META",
                "security_id": "META-new",
                "export_stem": "META-META-new",
                "last_update": "remote-new",
            },
        }
        transport = ManifestTransport(manifest)
        client = Client("test-key", cache_dir=Path(tmp))
        client._transport = transport

        data = client._fetch("free-float-events", "META")

        assert data["ticker"] == "META-META-new"
        assert transport.get_calls == [("free-float-events", "META-META-new")]


def test_fetch_canonicalizes_exact_security_id_alias_to_export_stem():
    with TemporaryDirectory() as tmp:
        manifest = {
            "META-new": {
                "ticker": "META",
                "security_id": "META-new",
                "export_stem": "META-META-new",
                "last_update": "remote-new",
            },
            "META-META-new": {
                "ticker": "META",
                "security_id": "META-new",
                "export_stem": "META-META-new",
                "last_update": "remote-new",
            },
        }
        transport = ManifestTransport(manifest)
        client = Client("test-key", cache_dir=Path(tmp))
        client._transport = transport

        data = client._fetch("free-float-events", "META-new")

        assert data["ticker"] == "META-META-new"
        assert transport.get_calls == [("free-float-events", "META-META-new")]


def test_fetch_raises_for_multiple_active_matches_for_same_ticker():
    with TemporaryDirectory() as tmp:
        manifest = {
            "META-one": {
                "ticker": "META",
                "security_id": "META-one",
                "last_update": "remote-1",
            },
            "META-two": {
                "ticker": "META",
                "security_id": "META-two",
                "last_update": "remote-2",
            },
        }
        client = Client("test-key", cache_dir=Path(tmp))
        client._transport = ManifestTransport(manifest)

        try:
            client._fetch("free-float", "META")
        except AmbiguousTickerError as exc:
            assert exc.ticker == "META"
            assert exc.endpoint == "free-float"
            assert exc.matches == ["META-one", "META-two"]
        else:
            raise AssertionError("Expected AmbiguousTickerError")


def _run_direct():
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"{name}: ok")


if __name__ == "__main__":
    _run_direct()
