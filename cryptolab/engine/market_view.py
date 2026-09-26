"""Point-in-time market data for the backtest loop (math.md §1 timing rule, FR-13).

``MarketData`` validates and precomputes everything once: a regular bar grid at the run's
timeframe, per-symbol numpy arrays, and for every grid index ``t`` how many of a symbol's
bars have closed by the decision time ``d_t``. ``PointInTimeView(data, t)`` is what a
strategy receives at ``d_t``: its accessors return *copies* of bars ``0..t`` only, so a
strategy cannot reach bar ``t+1`` or later through the public API, and cannot mutate the
engine's data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import numpy.typing as npt
import pandas as pd

from cryptolab.data.schema import OHLCV_COLUMNS

FloatArr = npt.NDArray[np.float64]
IntArr = npt.NDArray[np.int64]


@dataclass(frozen=True)
class SymbolData:
    frame: pd.DataFrame  # normalized OHLCV frame, RangeIndex, ts as datetime64[ns, UTC]
    open: FloatArr  # per symbol row
    close: FloatArr  # per symbol row
    ts: list[datetime]  # per symbol row, bar open time
    upto: IntArr  # per grid t: number of symbol rows with ts <= grid[t]
    row_at: IntArr  # per grid t: symbol row whose ts == grid[t], or -1 if missing
    close_ff: FloatArr  # per grid t: last close at or before grid[t] (math.md §4), NaN before


def _epoch_ns(idx: pd.DatetimeIndex) -> IntArr:
    """Nanoseconds since the epoch (UTC) of a tz-aware index."""
    naive = idx.tz_convert("UTC").tz_localize(None).as_unit("ns")
    return np.asarray(naive.to_numpy(), dtype="datetime64[ns]").view(np.int64)


def _normalize(symbol: str, df: pd.DataFrame, bar_ns: int) -> pd.DataFrame:
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{symbol}: missing OHLCV columns {missing}")
    if len(df) == 0:
        raise ValueError(f"{symbol}: no bars")
    ts = df["ts"]
    if not isinstance(ts.dtype, pd.DatetimeTZDtype):
        raise ValueError(f"{symbol}: ts must be tz-aware UTC datetimes")
    idx = pd.DatetimeIndex(ts).tz_convert("UTC").as_unit("ns")
    out = pd.DataFrame(
        {
            "ts": idx,
            **{c: df[c].to_numpy(dtype=np.float64, copy=True) for c in OHLCV_COLUMNS[1:]},
        }
    )
    ns = _epoch_ns(idx)
    if len(ns) > 1 and not bool(np.all(np.diff(ns) > 0)):
        raise ValueError(f"{symbol}: ts must be strictly increasing (sorted, no duplicates)")
    if bool(np.any(ns % bar_ns != 0)):
        raise ValueError(f"{symbol}: bar open times are not aligned to the timeframe grid")
    for col in ("open", "close"):
        v = out[col].to_numpy()
        if not bool(np.all(np.isfinite(v) & (v > 0))):
            raise ValueError(f"{symbol}: {col} must be finite and > 0")
    return out


class MarketData:
    """Validated, precomputed bars on a regular grid (engine-internal)."""

    def __init__(self, bars: Mapping[str, pd.DataFrame], bar_seconds: int) -> None:
        if not bars:
            raise ValueError("bars is empty")
        self.bar_ns = int(bar_seconds) * 1_000_000_000
        frames = {s: _normalize(s, bars[s], self.bar_ns) for s in sorted(bars)}
        ns = {s: _epoch_ns(pd.DatetimeIndex(f["ts"])) for s, f in frames.items()}
        start = min(int(a[0]) for a in ns.values())
        end = max(int(a[-1]) for a in ns.values())
        # A regular grid, so a timestamp missing for every symbol is still a bar (math.md §5.7).
        # Integer bar count: np.arange(start, end + 1, step) sizes itself in float64 and
        # silently drops the last bar(s) of long ns-epoch series.
        n_bars = (end - start) // self.bar_ns + 1
        self.grid_ns: IntArr = start + self.bar_ns * np.arange(n_bars, dtype=np.int64)
        grid_index = pd.DatetimeIndex(self.grid_ns.astype("datetime64[ns]")).tz_localize("UTC")
        self.index = grid_index
        self.bar_times: list[datetime] = list(grid_index.to_pydatetime())
        dec = grid_index + pd.Timedelta(self.bar_ns, unit="ns")
        self.decision_times: list[datetime] = list(dec.to_pydatetime())  # d_t = tau_t + Delta
        self.symbols: tuple[str, ...] = tuple(frames)
        self.sym: dict[str, SymbolData] = {}
        for s, f in frames.items():
            a = ns[s]
            upto = np.searchsorted(a, self.grid_ns, side="right").astype(np.int64)
            pos = np.searchsorted(a, self.grid_ns, side="left")
            hit = (pos < len(a)) & (a[np.minimum(pos, len(a) - 1)] == self.grid_ns)
            row_at = np.where(hit, pos, -1).astype(np.int64)
            close = f["close"].to_numpy()
            close_ff = np.where(upto > 0, close[np.maximum(upto - 1, 0)], np.nan)
            self.sym[s] = SymbolData(
                frame=f,
                open=f["open"].to_numpy(),
                close=close,
                ts=list(pd.DatetimeIndex(f["ts"]).to_pydatetime()),
                upto=upto,
                row_at=row_at,
                close_ff=close_ff.astype(np.float64),
            )

    def __len__(self) -> int:
        return len(self.grid_ns)


class PointInTimeView:
    """``MarketView`` at decision time ``d_t`` (math.md §1): only bars ``0..t`` are visible."""

    __slots__ = ("_data", "_t")

    def __init__(self, data: MarketData, t: int) -> None:
        if not 0 <= t < len(data):
            raise IndexError(t)
        self._data = data
        self._t = t

    @property
    def now(self) -> datetime:
        return self._data.decision_times[self._t]

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._data.symbols

    def bars(self, symbol: str, lookback: int | None = None) -> pd.DataFrame:
        """Copy of the last ``lookback`` bars of ``symbol`` with close <= now (all if None)."""
        sd = self._data.sym.get(symbol)
        if sd is None:
            raise KeyError(f"unknown symbol {symbol!r}; view has {self._data.symbols}")
        if lookback is not None and lookback < 0:
            raise ValueError("lookback must be >= 0")
        end = int(sd.upto[self._t])
        start = 0 if lookback is None else max(0, end - lookback)
        out = sd.frame.iloc[start:end].copy()
        out.index = pd.RangeIndex(end - start)
        return out
