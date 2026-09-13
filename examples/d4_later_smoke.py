"""Later manual D4-L smoke test against the approved D3c Preview.

This script makes hosted, potentially metered requests. It is intentionally not
part of the offline test suite and has NOT been run as part of D4-L. Importing
the module performs no I/O and makes no network calls.
"""

from __future__ import annotations

import getpass
import pprint
import tempfile
from typing import Any

from datawiserai import Client, DatawiserAPIError


APPROVED_PREVIEW_BASE_URL = (
    "https://datawiser-m7pbzbu6l-datawisers-projects.vercel.app/v1"
)


def _read_api_key() -> str:
    api_key = getpass.getpass("Datawiser API key: ")
    if not api_key:
        raise SystemExit("A Datawiser API key is required.")
    return api_key


def _print_identity(label: str, result: Any) -> None:
    print(
        f"{label}: ticker={result.ticker!r}, "
        f"security_id={result.security_id!r}"
    )
    raw = getattr(result, "raw", None)
    if isinstance(raw, dict):
        supplied_scope = {
            field: raw[field]
            for field in ("plan", "profile", "view")
            if field in raw
        }
        if supplied_scope:
            print(f"{label}: server-supplied scope={supplied_scope!r}")


def main() -> None:
    api_key = _read_api_key()
    with tempfile.TemporaryDirectory(prefix="datawiserai-d4-smoke-") as cache:
        client = Client(
            api_key,
            base_url=APPROVED_PREVIEW_BASE_URL,
            cache_dir=cache,
        )

        print("Shares outstanding: first request, then conditional request")
        so_first = client.shares_outstanding("AAPL")
        so_second = client.shares_outstanding("AAPL")
        _print_identity("SO first", so_first)
        _print_identity("SO second", so_second)

        print("Reference: first request, then conditional request")
        ref_first = client.reference("AAPL")
        ref_second = client.reference("AAPL")
        _print_identity("reference first", ref_first)
        _print_identity("reference second", ref_second)

        print("Client diagnostics:")
        pprint.pp(client.diagnostics)

        try:
            client.shares_outstanding("AAPL-CA")
        except DatawiserAPIError as exc:
            if exc.status_code not in {403, 404}:
                raise
            print(
                "AAPL-CA denied or unavailable to this caller "
                f"(HTTP {exc.status_code}); no fallback was used."
            )
        else:
            print(
                "AAPL-CA returned a currently authorised representation; "
                "the historical Preview denial is not a permanent rule."
            )


if __name__ == "__main__":
    main()
