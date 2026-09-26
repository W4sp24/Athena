"""Engine contracts shared with strategies and analytics (architecture.md §3).

These are the integration seams between modules built in parallel. Changing a
signature here needs a decision-log entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

import pandas as pd

Side = Literal["buy", "sell"]


class MarketView(Protocol):
    """Point-in-time, read-only view handed to a strategy at decision time.

    ``now`` is the decision time d_t: the close of the current bar (math.md §1).
    Every accessor returns only bars whose close is <= ``now``.
    """

    @property
    def now(self) -> datetime: ...

    @property
    def symbols(self) -> tuple[str, ...]: ...

    def bars(self, symbol: str, lookback: int | None = None) -> pd.DataFrame:
        """Last ``lookback`` completed bars for ``symbol`` (all of them if None).

        Columns follow cryptolab.data.schema.OHLCV_COLUMNS.
        """
        ...


class Strategy(Protocol):
    """One file per strategy (FR-09, NFR-04). Returns target weights; never orders."""

    name: str

    def on_bar(self, view: MarketView) -> dict[str, float]:
        """Return {symbol: target weight of equity}. Omitted symbols keep their position."""
        ...


@dataclass(frozen=True)
class Fill:
    """One executed (simulated) fill. Prices and fees in quote currency."""

    ts: datetime  # fill time = open of bar t+1 (math.md §5.2)
    decision_ts: datetime  # d_t, when the strategy decided
    symbol: str
    side: Side
    qty: float  # base units, always > 0
    price: float  # after slippage
    fee: float
    notes: str = ""  # e.g. "scaled_for_cash", "clipped"


@dataclass(frozen=True)
class BacktestResult:
    """Output of a backtest run, consumed by analytics.

    - ``equity``: pd.Series of V_t indexed by bar open ts (UTC), marked at close (math.md §4).
    - ``fills``: DataFrame with the Fill fields as columns, one row per fill, time-ordered.
    - ``rejections``: DataFrame of RiskGate rejections/clips (ts, symbol, reason, detail).
    """

    equity: pd.Series
    fills: pd.DataFrame
    rejections: pd.DataFrame
    initial_capital: float
    timeframe: str
    meta: dict[str, Any] = field(default_factory=dict)  # config hash, data range, git sha
