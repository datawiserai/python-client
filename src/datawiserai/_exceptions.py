from __future__ import annotations


class DatawiserError(Exception):
    """Base exception for all datawiserai errors."""


class DatawiserAPIError(DatawiserError):
    """Raised when the remote API returns a non-success response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"API error {status_code}: {message}")


class TickerNotFoundError(DatawiserError):
    """Raised when a ticker is not present in the endpoint manifest."""

    def __init__(self, ticker: str, endpoint: str) -> None:
        self.ticker = ticker
        self.endpoint = endpoint
        super().__init__(
            f"Ticker '{ticker}' not found in the '{endpoint}' manifest"
        )


class AmbiguousTickerError(DatawiserError):
    """Raised when a ticker cannot resolve to one active manifest entry."""

    def __init__(self, ticker: str, endpoint: str, matches: list[str]) -> None:
        self.ticker = ticker
        self.endpoint = endpoint
        self.matches = matches
        match_list = ", ".join(matches)
        super().__init__(
            f"Ticker '{ticker}' does not resolve to exactly one active security "
            f"in the '{endpoint}' manifest. Matching manifest keys: {match_list}. "
            "Use a security_id instead."
        )
