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
