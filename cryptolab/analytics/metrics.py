"""Performance metrics exactly as specified in docs/sdd/math.md §2-3 (FR-17).

Inputs follow the engine contract (cryptolab/engine/types.py):

- ``equity``: V_t indexed by bar open time, marked at close. ``equity.iloc[0]`` is V_0.
- ``fills``: one row per Fill (ts, decision_ts, symbol, side, qty, price, fee, notes).

Undefined quantities (zero variance, too few periods, no closed round trips) are NaN,
never inf and never an exception. Reports render NaN as "n/a".

Conventions where math.md leaves room (all chosen to not flatter a strategy):

- N is the number of return periods: ``len(equity) - 1``.
- A denominator (s(r) or DD) below ``_ZERO_TOL`` counts as zero, so the ratio is NaN.
  That includes a positive-mean series with no downside (Sortino would be +inf).
- The mean equity in turnover (§3.6) averages all T+1 marks, V_0 included.
- Win rate (§3.5): every FIFO match of an exit quantity against an open entry lot is one
  round trip. Both legs' fees are allocated pro rata to the matched quantity.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

__all__ = [
    "FifoResult",
    "Metrics",
    "RoundTrip",
    "compute_metrics",
    "drawdown_series",
    "fifo_round_trips",
    "periods_per_year",
]

# A std/downside deviation smaller than this is float noise around zero -> ratio undefined.
_ZERO_TOL = 1e-12
# Remaining quantity at or below this fraction of the original is float dust, not a position.
_DUST_REL = 1e-9

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
_REQUIRED_FILL_COLUMNS = ("ts", "symbol", "side", "qty", "price", "fee")

FloatArray = npt.NDArray[np.float64]


def periods_per_year(timeframe: str) -> int:
    """P for a timeframe string such as "1h" or "1d".

    math.md §1: crypto trades 24/7, so P = 365 days * 86400 s / bar seconds
    (8760 hourly, 365 daily). Same formula as ``cryptolab.data.schema.PERIODS_PER_YEAR``.
    """
    match = re.fullmatch(r"(\d+)([mhdw])", timeframe)
    if match is None or int(match.group(1)) == 0:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    bar_seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    return 365 * 86400 // bar_seconds  # math.md §1


@dataclass(frozen=True)
class Metrics:
    """Every metric of math.md §2-3 for one equity curve and its fills."""

    total_return: float
    cagr: float
    volatility_ann: float
    sharpe: float
    sortino: float
    max_drawdown: float
    max_drawdown_duration_bars: int
    win_rate: float
    n_round_trips: int
    n_open_positions: int
    turnover_ann: float
    n_fills: int
    total_fees: float
    start: pd.Timestamp
    end: pd.Timestamp
    n_periods: int


@dataclass(frozen=True)
class RoundTrip:
    """One closed FIFO match (math.md §3.5). ``pnl`` is net of both legs' fees."""

    symbol: str
    direction: Literal["long", "short"]
    qty: float
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry_price: float
    exit_price: float
    fees: float
    pnl: float


@dataclass(frozen=True)
class FifoResult:
    round_trips: tuple[RoundTrip, ...]
    open_positions: dict[str, float]  # symbol -> signed residual qty (negative = short)
    has_shorts: bool  # any fill opened a short lot (D-011)


@dataclass
class _Lot:
    ts: pd.Timestamp
    sign: int  # +1 long, -1 short
    qty: float  # remaining, unsigned
    orig_qty: float
    price: float
    fee_per_unit: float


def _has_fills(fills: pd.DataFrame) -> bool:
    if len(fills) == 0:
        return False
    missing = [c for c in _REQUIRED_FILL_COLUMNS if c not in fills.columns]
    if missing:
        raise ValueError(f"fills is missing columns {missing}")
    return True


def fifo_round_trips(fills: pd.DataFrame) -> FifoResult:
    """Pair fills into round trips per symbol, FIFO (math.md §3.5).

    Longs (buy first) and simulated shorts (sell first, D-011) are both supported; a fill
    larger than the open position closes it and opens a new lot the other way.
    """
    if not _has_fills(fills):
        return FifoResult(round_trips=(), open_positions={}, has_shorts=False)

    books: dict[str, deque[_Lot]] = {}
    trips: list[RoundTrip] = []
    has_shorts = False
    ordered = fills.sort_values("ts", kind="stable")
    for ts, symbol, side, raw_qty, raw_price, fee in zip(
        ordered["ts"],
        ordered["symbol"],
        ordered["side"],
        ordered["qty"],
        ordered["price"],
        ordered["fee"],
        strict=True,
    ):
        if side not in ("buy", "sell"):
            raise ValueError(f"unknown fill side {side!r}")
        qty = float(raw_qty)
        price = float(raw_price)
        if not qty > 0:
            raise ValueError(f"fill qty must be > 0, got {qty}")
        sign = 1 if side == "buy" else -1
        exit_fee_per_unit = float(fee) / qty
        book = books.setdefault(str(symbol), deque())
        remaining = qty

        # Close opposite-signed lots, oldest first.
        while book and book[0].sign != sign and remaining > _DUST_REL * qty:
            lot = book[0]
            matched = min(lot.qty, remaining)
            # math.md §3.5: net of both legs' fees, allocated pro rata to matched qty.
            fees = matched * (lot.fee_per_unit + exit_fee_per_unit)
            gross = lot.sign * matched * (price - lot.price)
            trips.append(
                RoundTrip(
                    symbol=str(symbol),
                    direction="long" if lot.sign > 0 else "short",
                    qty=matched,
                    entry_ts=pd.Timestamp(lot.ts),
                    exit_ts=pd.Timestamp(ts),
                    entry_price=lot.price,
                    exit_price=price,
                    fees=fees,
                    pnl=gross - fees,
                )
            )
            lot.qty -= matched
            remaining -= matched
            if lot.qty <= _DUST_REL * lot.orig_qty:
                book.popleft()

        # Whatever is left opens (or adds to) a position in the fill's direction.
        if remaining > _DUST_REL * qty:
            book.append(
                _Lot(
                    ts=pd.Timestamp(ts),
                    sign=sign,
                    qty=remaining,
                    orig_qty=remaining,
                    price=price,
                    fee_per_unit=exit_fee_per_unit,
                )
            )
            has_shorts = has_shorts or sign < 0

    open_positions = {
        sym: sum(lot.sign * lot.qty for lot in book) for sym, book in books.items() if book
    }
    return FifoResult(
        round_trips=tuple(trips), open_positions=open_positions, has_shorts=has_shorts
    )


def drawdown_series(equity: pd.Series) -> pd.Series:
    """D_t = V_t / M_t - 1 with M_t the running high-water mark (math.md §3.4)."""
    values = equity.to_numpy(dtype="float64")
    hwm = np.maximum.accumulate(values)  # math.md §3.4: M_t = max_{s<=t} V_s
    return pd.Series(values / hwm - 1.0, index=equity.index, name="drawdown")


def _longest_negative_run(dd: FloatArray) -> int:
    longest = current = 0
    for d in dd:
        current = current + 1 if d < 0 else 0
        longest = max(longest, current)
    return longest


def _ratio(numerator: float, denominator: float, scale: float) -> float:
    if not math.isfinite(denominator) or denominator < _ZERO_TOL:
        return math.nan
    return numerator / denominator * scale


def _cagr(growth: float, p: int, n: int) -> float:
    if n == 0:
        return math.nan
    try:
        return float(growth ** (p / n) - 1.0)  # math.md §2.4
    except OverflowError:
        return math.inf


def compute_metrics(
    equity: pd.Series,
    fills: pd.DataFrame,
    timeframe: str,
    risk_free_annual: float = 0.0,
) -> Metrics:
    """Compute every math.md §2-3 metric for one run. See the module docstring."""
    if len(equity) == 0:
        raise ValueError("equity is empty")
    v: FloatArray = equity.to_numpy(dtype="float64")
    if np.isnan(v).any():
        raise ValueError("equity contains NaN")
    if not v[0] > 0:
        raise ValueError("initial equity V_0 must be positive")

    p = periods_per_year(timeframe)
    sqrt_p = math.sqrt(p)
    r: FloatArray = v[1:] / v[:-1] - 1.0  # math.md §2.1
    n = len(r)

    total_return = float(v[-1] / v[0] - 1.0)  # math.md §2.3
    cagr = _cagr(float(v[-1] / v[0]), p, n)  # math.md §2.4

    # math.md §3.1: sample std (ddof=1) of simple returns, times sqrt(P).
    s = float(np.std(r, ddof=1)) if n >= 2 else math.nan
    volatility_ann = s * sqrt_p

    # math.md §3.2: per-period rf = (1 + rf)^(1/P) - 1; arithmetic mean of excess returns.
    rf_p = (1.0 + risk_free_annual) ** (1.0 / p) - 1.0
    excess_mean = float(np.mean(r - rf_p)) if n >= 1 else math.nan
    sharpe = _ratio(excess_mean, s, sqrt_p)

    # math.md §3.3: theta = rf_p; downside deviation divides by ALL N periods.
    if n >= 1:
        downside = np.minimum(0.0, r - rf_p)
        dd_dev = math.sqrt(float(np.sum(downside**2)) / n)
    else:
        dd_dev = math.nan
    sortino = _ratio(excess_mean, dd_dev, sqrt_p)

    # math.md §3.4: MDD = min_t D_t (<= 0); duration = longest run of bars with D_t < 0.
    dd = drawdown_series(equity).to_numpy(dtype="float64")
    max_drawdown = float(dd.min())
    max_dd_duration = _longest_negative_run(dd)

    # math.md §3.5: FIFO round trips, open positions excluded and counted separately.
    fifo = fifo_round_trips(fills)
    n_rt = len(fifo.round_trips)
    wins = sum(1 for rt in fifo.round_trips if rt.pnl > 0)
    win_rate = wins / n_rt if n_rt else math.nan

    has_fills = _has_fills(fills)
    # math.md §3.6: sum |q p| / mean(V) * P / N.
    notional = (
        float((fills["qty"].astype("float64") * fills["price"].astype("float64")).abs().sum())
        if has_fills
        else 0.0
    )
    turnover_ann = notional / float(np.mean(v)) * p / n if n else math.nan
    total_fees = float(fills["fee"].astype("float64").sum()) if has_fills else 0.0

    return Metrics(
        total_return=total_return,
        cagr=cagr,
        volatility_ann=volatility_ann,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        max_drawdown_duration_bars=max_dd_duration,
        win_rate=win_rate,
        n_round_trips=n_rt,
        n_open_positions=len(fifo.open_positions),
        turnover_ann=turnover_ann,
        n_fills=len(fills) if has_fills else 0,
        total_fees=total_fees,
        start=pd.Timestamp(equity.index[0]),
        end=pd.Timestamp(equity.index[-1]),
        n_periods=n,
    )
