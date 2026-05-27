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

from datawiserai import Client


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


def _run_direct():
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"{name}: ok")


if __name__ == "__main__":
    _run_direct()
