from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple


class FileCache:
    """Simple file-based cache: one gzipped JSON file per (endpoint, ticker) pair.

    Layout::

        cache_dir/
          free-float/
            OLP.json.gz        # {"last_update": "...", "cached_at": "...", "data": {...}}
          shares-outstanding/
            OLP.json.gz
    """

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, endpoint: str, ticker: str) -> Path:
        return self._dir / endpoint / f"{ticker}.json.gz"

    def get(
        self, endpoint: str, ticker: str
    ) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
        """Return ``(cached_data, cached_last_update)`` or ``(None, None)``."""
        path = self._path(endpoint, ticker)
        if not path.exists():
            return None, None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                entry = json.load(f)
            return entry["data"], entry["last_update"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None, None

    def put(
        self,
        endpoint: str,
        ticker: str,
        data: dict[str, Any],
        last_update: str,
    ) -> None:
        """Write *data* to the cache with its manifest *last_update* stamp."""
        path = self._path(endpoint, ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "last_update": last_update,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(entry, f)

    def clear(self, endpoint: str | None = None) -> int:
        """Delete cached files.  Returns the number of files removed.

        If *endpoint* is given only that endpoint's cache is cleared;
        otherwise the entire cache is wiped.
        """
        root = self._dir / endpoint if endpoint else self._dir
        removed = 0
        if not root.exists():
            return removed
        for p in root.rglob("*.json.gz"):
            p.unlink()
            removed += 1
        for d in sorted(root.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        return removed
