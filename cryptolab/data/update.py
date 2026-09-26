"""Incremental OHLCV update (FR-02).

For each symbol: resume from the bar after the last stored one, or from
``start`` when nothing is stored yet. Re-running with no new closed bars adds
nothing and rewrites no files.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from cryptolab.data.download import fetch_ohlcv_range
from cryptolab.data.exchange import OhlcvSource, make_exchange
from cryptolab.data.frame import require_utc, timeframe_delta
from cryptolab.data.store import OhlcvStore


def update(
    store: OhlcvStore,
    exchange_id: str,
    symbols: Iterable[str],
    timeframe: str,
    start: datetime,
    *,
    exchange: OhlcvSource | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Bring each ``symbol`` up to the last closed bar; returns ``{symbol: rows_added}``.

    ``exchange`` may be injected (tests); otherwise a public client for
    ``exchange_id`` is created. Data is stored under ``exchange_id``. Only
    forward extension is done: a ``start`` earlier than the stored history is
    not back-filled.
    """
    require_utc(start, "start")
    step = timeframe_delta(timeframe)
    ex = exchange if exchange is not None else make_exchange(exchange_id)
    added: dict[str, int] = {}
    for symbol in symbols:
        last = store.last_ts(exchange_id, symbol, timeframe)
        since = last + step if last is not None else start
        df = fetch_ohlcv_range(ex, symbol, timeframe, since, now=now)
        added[symbol] = store.write(exchange_id, symbol, timeframe, df)
    return added
