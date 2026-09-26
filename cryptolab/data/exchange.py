"""CCXT exchange factory for public market data (FR-01, FR-04).

Only public endpoints are used: no API keys are ever passed here. The exchange
id is a parameter so the data source can fall back (D-013: binance, then okx).
"""

from __future__ import annotations

from typing import Any, Protocol

import ccxt

DEFAULT_EXCHANGE = "binance"
FALLBACK_EXCHANGE = "okx"


class OhlcvSource(Protocol):
    """The slice of ``ccxt.Exchange`` the data layer relies on (lets tests inject fakes)."""

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = ...,
        since: int | None = ...,
        limit: int | None = ...,
        params: dict[str, Any] | None = ...,
    ) -> list[list[float]]: ...


def make_exchange(exchange_id: str) -> ccxt.Exchange:
    """Return an unauthenticated CCXT client with rate limiting on."""
    if exchange_id not in ccxt.exchanges:
        raise ValueError(f"unknown ccxt exchange id {exchange_id!r}")
    cls = getattr(ccxt, exchange_id)
    return cls({"enableRateLimit": True})
