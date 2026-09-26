"""Paginated OHLCV download over a date range (FR-01).

Returns closed bars only: a bar whose close (``ts + timeframe``) is after ``now``
is still forming and is dropped. Gaps in the exchange's history are left as
gaps (never forward-filled); ``quality.quality_report`` reports them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

import ccxt
import pandas as pd

from cryptolab.data.exchange import OhlcvSource
from cryptolab.data.frame import from_ccxt_rows, require_utc, timeframe_seconds

DEFAULT_LIMIT = 1000


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _fetch_page(
    exchange: OhlcvSource,
    symbol: str,
    timeframe: str,
    *,
    since: int,
    limit: int,
    max_retries: int,
    backoff: float,
    sleep: Callable[[float], None],
) -> list[list[float]]:
    """One ``fetch_ohlcv`` call, retrying transient ``ccxt.NetworkError`` with backoff."""
    attempt = 0
    while True:
        try:
            return exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except ccxt.NetworkError:
            if attempt >= max_retries:
                raise
            sleep(backoff * 2**attempt)
            attempt += 1


def fetch_ohlcv_range(
    exchange: OhlcvSource,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime | None = None,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
    max_retries: int = 3,
    backoff: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Download closed bars with ``start <= ts < end`` (``ts`` = bar open time, UTC).

    Pages forward with ``since``/``limit`` until the exchange returns an empty
    page, stops making progress, or the range is covered. ``end=None`` means
    "up to the last closed bar". ``now`` defaults to the current UTC time and
    exists for tests. The result follows ``cryptolab.data.schema``.
    """
    step_ms = timeframe_seconds(timeframe) * 1000
    require_utc(start, "start")
    if end is not None:
        require_utc(end, "end")
    now = require_utc(now, "now") if now is not None else datetime.now(UTC)

    start_ms = _ms(start)
    end_ms = _ms(end) if end is not None else None
    last_closed_open_ms = _ms(now) - step_ms  # latest open ts whose bar has closed
    last_wanted_ms = last_closed_open_ms if end_ms is None else min(end_ms - 1, last_closed_open_ms)

    rows: list[list[float]] = []
    since = start_ms
    while since <= last_wanted_ms:
        page = _fetch_page(
            exchange,
            symbol,
            timeframe,
            since=since,
            limit=limit,
            max_retries=max_retries,
            backoff=backoff,
            sleep=sleep,
        )
        if not page:
            break
        rows.extend(page)
        last = int(max(r[0] for r in page))
        if last < since:  # exchange ignored `since`; avoid looping forever
            break
        since = last + step_ms

    df = from_ccxt_rows(rows)
    ts_ms = df["ts"].astype("int64") // 1_000_000
    keep = (ts_ms >= start_ms) & (ts_ms <= last_wanted_ms)
    return df.loc[keep].reset_index(drop=True)
