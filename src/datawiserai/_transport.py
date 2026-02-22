from __future__ import annotations

from typing import Any

import requests

from ._exceptions import DatawiserAPIError

BASE_URL = "https://api.datawiser.ai/v1"


class Transport:
    """Fetch data from the Datawiser API over HTTPS."""

    def __init__(self, api_key: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-API-Key": api_key,
                "Accept": "application/json",
            }
        )

    def _request(self, url: str) -> dict[str, Any]:
        resp = self._session.get(url, timeout=30)
        if not resp.ok:
            raise DatawiserAPIError(resp.status_code, resp.text)
        return resp.json()

    def get_manifest(self, endpoint: str) -> dict[str, Any]:
        """Return the folder manifest (last_update per ticker) for *endpoint*."""
        return self._request(f"{BASE_URL}/manifest/{endpoint}/updates")

    def get(self, endpoint: str, ticker: str) -> dict[str, Any]:
        """Return the JSON payload for a single *ticker* under *endpoint*."""
        return self._request(f"{BASE_URL}/{endpoint}/{ticker}")
