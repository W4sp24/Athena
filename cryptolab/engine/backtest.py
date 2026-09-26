"""Event-driven backtest loop (FR-13..FR-16, NFR-01, NFR-02). Implements math.md §4-§5.

Per bar ``t`` (grid of the run's timeframe, bars identified by open time ``tau_t``):

1. Fill the orders decided at ``d_{t-1}`` at ``O_t`` (math.md §5.2-§5.5), sells before buys.
   An order whose symbol has no bar ``t`` expires unfilled and is logged (§5.7).
2. Mark to market at ``C_t`` with last-close carry-forward for missing bars (§4):
   ``V_t = c_t + sum_i q_i C_i``.
3. ``RiskGate.on_mark(V_t, d_t)``: DAILY_LOSS / MAX_DRAWDOWN kill-switch triggers.
4. The strategy sees bars ``0..t`` at ``d_t = tau_t + Delta`` and returns target weights.
5. Targets become order intents (§5.1), each checked by RiskGate. Approved orders wait for
   ``O_{t+1}``. At the final bar there is no next open, so signals are dropped and logged.

Deterministic: no randomness; symbols and orders are processed in a fixed order (NFR-01).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from cryptolab.config import RiskLimits, TradingMode
from cryptolab.data.schema import TIMEFRAME_SECONDS
from cryptolab.engine.market_view import MarketData, PointInTimeView
from cryptolab.engine.types import BacktestResult, Side, Strategy
from cryptolab.risk import (
    InMemoryKillSwitch,
    OrderIntent,
    PortfolioState,
    Reason,
    RiskGate,
)

FILL_COLUMNS = ("ts", "decision_ts", "symbol", "side", "qty", "price", "fee", "notes")
REJECTION_COLUMNS = ("ts", "symbol", "reason", "detail")

# Engine-side log reasons (RiskGate reasons are cryptolab.risk.Reason values).
DROPPED_LAST_BAR = "DROPPED_LAST_BAR"  # math.md §5.7
EXPIRED_NO_NEXT_BAR = "EXPIRED_NO_NEXT_BAR"  # math.md §5.7
NO_CASH = "NO_CASH"  # math.md §5.5, nothing left to buy with
SCALED_BELOW_MIN_NOTIONAL = "SCALED_BELOW_MIN_NOTIONAL"  # §5.5 scaling left dust (§5.1)
UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
INVALID_WEIGHT = "INVALID_WEIGHT"
NO_PRICE = "NO_PRICE"  # symbol has no bar yet at decision time

SHORT_NOTE = "simulated shorts used; no borrow fee is modelled (math.md §5.6, optimistic)"


@dataclass(frozen=True)
class _Order:
    symbol: str
    side: Side
    qty: float
    decision_ts: datetime
    notes: str


@dataclass(frozen=True)
class _Sized:
    symbol: str
    side: Side
    qty: float
    ref_price: float
    bar_ts: datetime
    weight: float


class _Run:
    """Mutable state of one backtest run. Private: use ``run_backtest``."""

    def __init__(
        self,
        data: MarketData,
        strategy: Strategy,
        gate: RiskGate,
        *,
        initial_capital: float,
        fee: float,
        slip: float,
        min_notional: float,
    ) -> None:
        self.data = data
        self.strategy = strategy
        self.gate = gate
        self.f = fee
        self.s = slip
        self.min_notional = min_notional
        self.cash = float(initial_capital)
        self.pos: dict[str, float] = dict.fromkeys(data.symbols, 0.0)
        self.pending: list[_Order] = []
        self.fills: list[tuple[Any, ...]] = []
        self.rejections: list[tuple[Any, ...]] = []
        self.skipped_dust = 0
        self.used_short = False

    # ------------------------------------------------------------------ loop
    def run(self) -> np.ndarray[Any, np.dtype[np.float64]]:
        n = len(self.data)
        equity = np.empty(n, dtype=np.float64)
        for t in range(n):
            if self.pending:
                self._execute(t)
            v = self._mark(t)
            equity[t] = v
            d_t = self.data.decision_times[t]
            self.gate.on_mark(v, d_t)
            weights = self.strategy.on_bar(PointInTimeView(self.data, t))
            sized = self._size(weights, t, v)
            if not sized:
                continue
            if t == n - 1:
                # math.md §5.7: no next open to fill at; drop and log.
                for o in sized:
                    self._reject(d_t, o.symbol, DROPPED_LAST_BAR, _describe(o, "no bar t+1"))
                continue
            self._gate(sized, t, v)
        return equity

    # ------------------------------------------------------ 1. fills (§5.2-§5.5)
    def _execute(self, t: int) -> None:
        tau = self.data.bar_times[t]
        orders, self.pending = self.pending, []
        for o in orders:  # queued sells first, then buys (math.md §5.5)
            sd = self.data.sym[o.symbol]
            row = int(sd.row_at[t])
            if row < 0:
                # math.md §5.7: never fill at a stale or future price.
                self._reject(tau, o.symbol, EXPIRED_NO_NEXT_BAR, f"{o.side} {o.qty:.10g}")
                continue
            open_px = float(sd.open[row])
            qty, notes = o.qty, o.notes
            if o.side == "sell":
                price = open_px * (1.0 - self.s)  # §5.2 p_sell = O_{t+1}(1 - s)
                fee = self.f * qty * price  # §5.3
                self.cash += qty * price - fee  # §5.4
                self.pos[o.symbol] -= qty
                if self.pos[o.symbol] < 0:
                    self.used_short = True  # §5.6 (backtest only; RiskGate enforces)
            else:
                price = open_px * (1.0 + self.s)  # §5.2 p_buy = O_{t+1}(1 + s)
                if qty * price + self.f * qty * price > self.cash:
                    # §5.5 affordability: dq = c / (p (1 + f))
                    if self.cash <= 0:
                        self._reject(tau, o.symbol, NO_CASH, f"buy {qty:.10g}, cash {self.cash}")
                        continue
                    qty = self.cash / (price * (1.0 + self.f))
                    notes = _join(notes, "scaled_for_cash")
                    if qty * price < self.min_notional:
                        self._reject(
                            tau,
                            o.symbol,
                            SCALED_BELOW_MIN_NOTIONAL,
                            f"buy scaled {o.qty:.10g} -> {qty:.10g}, below dust floor",
                        )
                        continue
                fee = self.f * qty * price  # §5.3
                self.cash -= qty * price + fee  # §5.4
                self.pos[o.symbol] += qty
            self.fills.append((tau, o.decision_ts, o.symbol, o.side, qty, price, fee, notes))

    # ----------------------------------------------------- 2. mark to market (§4)
    def _mark(self, t: int) -> float:
        v = self.cash
        for sym, q in self.pos.items():
            if q != 0.0:
                v += q * float(self.data.sym[sym].close_ff[t])  # carry forward last close
        return v

    # ------------------------------------------------- 5. targets -> orders (§5.1)
    def _size(self, weights: Mapping[str, float], t: int, equity: float) -> list[_Sized]:
        d_t = self.data.decision_times[t]
        if not isinstance(weights, Mapping):
            raise TypeError(f"strategy returned {type(weights).__name__}, expected a mapping")
        sells: list[_Sized] = []
        buys: list[_Sized] = []
        for sym in sorted(weights, key=str):
            sd = self.data.sym.get(sym) if isinstance(sym, str) else None
            if sd is None:
                self._reject(d_t, str(sym), UNKNOWN_SYMBOL, "not in this run's bars")
                continue
            try:
                w = float(weights[sym])
            except (TypeError, ValueError):
                w = math.nan
            if not math.isfinite(w):
                self._reject(d_t, sym, INVALID_WEIGHT, f"weight {weights[sym]!r}")
                continue
            price = float(sd.close_ff[t])
            if not math.isfinite(price):
                self._reject(d_t, sym, NO_PRICE, "no bar at or before decision time")
                continue
            target = w * equity / price  # §5.1 q* = w* V_t / C_t
            dq = target - self.pos[sym]
            if abs(dq) * price < self.min_notional:  # §5.1 dust skip
                if dq != 0.0:
                    self.skipped_dust += 1
                continue
            bar_ts = sd.ts[int(sd.upto[t]) - 1]
            side: Side = "buy" if dq > 0 else "sell"
            o = _Sized(sym, side, abs(dq), price, bar_ts, w)
            (buys if side == "buy" else sells).append(o)
        return sells + buys

    # ------------------------------------------------------------ RiskGate
    def _gate(self, sized: list[_Sized], t: int, equity: float) -> None:
        d_t = self.data.decision_times[t]
        name = getattr(self.strategy, "name", type(self.strategy).__name__)
        # Projected state: each approved intent counts against later ones (conservative).
        proj = dict(self.pos)
        marks = {s: float(sd.close_ff[t]) for s, sd in self.data.sym.items() if sd.upto[t] > 0}
        cash = self.cash
        for o in sized:
            intent = OrderIntent(
                strategy=name,
                symbol=o.symbol,
                side=o.side,
                qty=o.qty,
                ref_price=o.ref_price,
                decision_ts=d_t,
                bar_ts=o.bar_ts,
                signal_inputs={
                    "target_weight": o.weight,
                    "equity": equity,
                    "position": self.pos[o.symbol],
                    "close": o.ref_price,
                },
            )
            state = PortfolioState(cash=cash, positions=proj, marks=marks, equity=equity)
            verdict = self.gate.check(intent, state, d_t)
            if not verdict.approved:
                self._reject(d_t, o.symbol, verdict.reason.value, _describe(o, verdict.detail))
                continue
            qty = verdict.final_qty(intent)
            notes = ""
            if verdict.reason is Reason.CLIPPED:
                notes = "clipped"
                self._reject(
                    d_t,
                    o.symbol,
                    Reason.CLIPPED.value,
                    _describe(o, f"-> {qty:.10g}; {verdict.detail}"),
                )
            sign = 1.0 if o.side == "buy" else -1.0
            proj = {**proj, o.symbol: proj[o.symbol] + sign * qty}
            cash -= sign * qty * o.ref_price
            self.pending.append(_Order(o.symbol, o.side, qty, d_t, notes))

    def _reject(self, ts: datetime, symbol: str, reason: str, detail: str) -> None:
        self.rejections.append((ts, symbol, reason, detail))


def _join(a: str, b: str) -> str:
    return f"{a};{b}" if a else b


def _describe(o: _Sized, detail: str) -> str:
    return f"{o.side} {o.qty:.10g} @ {o.ref_price:.10g} (w={o.weight:g}): {detail}"


def _frame(
    rows: list[tuple[Any, ...]], columns: tuple[str, ...], ts_cols: tuple[str, ...]
) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(columns))
    for c in ts_cols:
        df[c] = pd.to_datetime(df[c], utc=True).dt.as_unit("ns")
    for c in ("qty", "price", "fee"):
        if c in df.columns:
            df[c] = df[c].astype(np.float64)
    for c in ("symbol", "side", "notes", "reason", "detail"):
        if c in df.columns:
            df[c] = df[c].astype(object)
    return df


def _check_params(
    timeframe: str, initial_capital: float, fee_bps: float, slippage_bps: float
) -> int:
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(
            f"unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAME_SECONDS)}"
        )
    if not (math.isfinite(initial_capital) and initial_capital > 0):
        raise ValueError("initial_capital must be finite and > 0")
    for label, v in (("fee_bps", fee_bps), ("slippage_bps", slippage_bps)):
        if not (math.isfinite(v) and 0 <= v < 10_000):
            raise ValueError(f"{label} must be finite and in [0, 10000)")
    return TIMEFRAME_SECONDS[timeframe]


def _strategy_params(strategy: Strategy) -> dict[str, Any]:
    try:
        attrs = vars(strategy)
    except TypeError:
        return {}
    return {k: v for k, v in sorted(attrs.items()) if not k.startswith("_")}


def run_backtest(
    bars: dict[str, pd.DataFrame],
    strategy: Strategy,
    *,
    timeframe: str,
    initial_capital: float,
    fee_bps: float,
    slippage_bps: float,
    risk_limits: RiskLimits,
    mode: TradingMode = TradingMode.BACKTEST,
    meta: dict[str, Any] | None = None,
) -> BacktestResult:
    """Run ``strategy`` over ``bars`` ({symbol: OHLCV frame}) and return the result.

    Every order passes RiskGate (D-004) with ``risk_limits``; ``mode`` changes what RiskGate
    allows (shorts only in BACKTEST, D-011). Fees and slippage are always charged.
    """
    bar_seconds = _check_params(timeframe, initial_capital, fee_bps, slippage_bps)
    data = MarketData(bars, bar_seconds)
    kill_switch = InMemoryKillSwitch()
    gate = RiskGate(risk_limits, mode, kill_switch, universe=data.symbols, bar_seconds=bar_seconds)
    run = _Run(
        data,
        strategy,
        gate,
        initial_capital=initial_capital,
        fee=fee_bps / 1e4,  # math.md §5.3 f = fee_bps / 10^4
        slip=slippage_bps / 1e4,  # math.md §5.2 s = slippage_bps / 10^4
        min_notional=risk_limits.min_order_notional,
    )
    equity_values = run.run()

    equity = pd.Series(equity_values, index=data.index, name="equity")
    fills = _frame(run.fills, FILL_COLUMNS, ("ts", "decision_ts"))
    rejections = _frame(run.rejections, REJECTION_COLUMNS, ("ts",))

    carried = {
        s: int(np.count_nonzero((sd.upto > 0) & (sd.row_at < 0))) for s, sd in data.sym.items()
    }
    config = {
        "timeframe": timeframe,
        "initial_capital": float(initial_capital),
        "fee_bps": float(fee_bps),
        "slippage_bps": float(slippage_bps),
        "mode": TradingMode(mode).value,
        "strategy": getattr(strategy, "name", type(strategy).__name__),
        "strategy_params": _strategy_params(strategy),
        "risk_limits": risk_limits.model_dump(mode="json"),
    }
    config_json = json.dumps(config, sort_keys=True, default=str)
    ks = kill_switch.state()
    engine_meta: dict[str, Any] = {
        **config,
        "config_hash": hashlib.sha256(config_json.encode()).hexdigest(),
        "data": {
            s: {
                "start": sd.ts[0].isoformat(),
                "end": sd.ts[-1].isoformat(),
                "rows": len(sd.ts),
            }
            for s, sd in data.sym.items()
        },
        "bars": len(data),
        "carried_forward_marks": carried,  # math.md §4 carry-forward count per symbol
        "carried_forward_marks_total": sum(carried.values()),
        "skipped_dust_orders": run.skipped_dust,
        "uses_shorts": run.used_short,
        "kill_switch": {
            "tripped": ks.tripped,
            "reason": ks.reason.value if ks.reason else None,
            "ts": ks.ts.isoformat() if ks.ts else None,
            "detail": ks.detail,
        },
    }
    if run.used_short:
        engine_meta["short_note"] = SHORT_NOTE
    return BacktestResult(
        equity=equity,
        fills=fills,
        rejections=rejections,
        initial_capital=float(initial_capital),
        timeframe=timeframe,
        meta={**(meta or {}), **engine_meta},  # engine keys win: they describe this run
    )
