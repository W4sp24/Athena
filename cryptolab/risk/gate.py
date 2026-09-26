"""RiskGate: every order intent passes here before it can fill (risk-model.md §2, D-004).

Check order (§2), stopping at the first failure:

1. kill switch armed?                      -> KILL_SWITCH
2. intent well-formed; mode allows side?   -> INVALID_ORDER / SHORT_NOT_ALLOWED
   Shorts are *always* rejected in PAPER and TESTNET, whatever ``allow_short`` says (D-011).
3. symbol whitelisted?                     -> SYMBOL_NOT_ALLOWED
4. data fresh?                             -> STALE_DATA
5. order-rate limit                        -> ORDER_RATE
6. per-order notional                      -> ORDER_NOTIONAL (rejected, never clipped)
7. post-fill per-symbol % and notional     -> clipped, or POSITION_LIMIT if nothing is left
8. post-fill gross exposure                -> clipped, or GROSS_EXPOSURE if nothing is left
9. dust floor on the final qty             -> BELOW_MIN_NOTIONAL (math.md §5.1)

Limits never enlarge an order. Any unexpected error rejects (NFR-07, fail closed).

``on_mark`` evaluates the DAILY_LOSS and MAX_DRAWDOWN triggers (§3) on every mark.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from cryptolab.config import RiskLimits, TradingMode
from cryptolab.risk.kill_switch import KillSwitch, RiskMarks, TripReason
from cryptolab.risk.types import OrderIntent, PortfolioState, Reason, Verdict

_HOUR = timedelta(hours=1)


def _is_utc_aware(ts: object) -> bool:
    return isinstance(ts, datetime) and ts.tzinfo is not None and ts.utcoffset() is not None


def _sign(intent: OrderIntent) -> float:
    return 1.0 if intent.side == "buy" else -1.0


def _reject(reason: Reason, detail: str) -> Verdict:
    return Verdict(approved=False, reason=reason, adjusted_qty=None, detail=detail)


class RiskGate:
    """Holds the frozen limits; strategies never see this object (FR-24)."""

    def __init__(
        self,
        limits: RiskLimits,
        mode: TradingMode,
        kill_switch: KillSwitch,
        *,
        universe: Iterable[str] | None = None,
        bar_seconds: int | None = None,
    ) -> None:
        """
        Args:
            universe: the symbols of this run; used when ``limits.allowed_symbols`` is None.
                If both are None, every symbol is rejected (fail closed).
            bar_seconds: bar length; required when ``limits.stale_data_seconds`` is None,
                which means the risk-model §2 default of 2 x bar length.
        """
        self.limits = limits
        self.mode = TradingMode(mode)
        self.kill_switch = kill_switch
        if limits.allowed_symbols is not None:
            self._whitelist: frozenset[str] = frozenset(limits.allowed_symbols)
        else:
            self._whitelist = frozenset(universe or ())
        if limits.stale_data_seconds is not None:
            stale = limits.stale_data_seconds
        elif bar_seconds is not None and bar_seconds > 0:
            stale = 2 * bar_seconds  # risk-model §2 default: 2 x bar length
        else:
            raise ValueError("stale_data_seconds is null: RiskGate needs bar_seconds > 0")
        self._stale = timedelta(seconds=stale)
        self._shorts_ok = self.mode is TradingMode.BACKTEST and limits.allow_short
        self._approvals: deque[datetime] = deque()

    # ----------------------------------------------------------------- check
    def check(self, intent: OrderIntent, state: PortfolioState, now: datetime) -> Verdict:
        try:
            verdict = self._check(intent, state, now)
        except Exception as exc:  # fail closed (NFR-07)
            return _reject(Reason.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        if verdict.approved:
            self._approvals.append(now)
        return verdict

    def _check(self, intent: OrderIntent, state: PortfolioState, now: datetime) -> Verdict:
        # risk-model.md §2 check order; each step returns a rejection or None.
        for step in (
            self._step_kill_switch,
            self._step_side,
            self._step_whitelist,
            self._step_fresh,
            self._step_rate,
            self._step_order_notional,
        ):
            rejection = step(intent, state, now)
            if rejection is not None:
                return rejection
        return self._step_exposure_and_dust(intent, state)

    # 1. kill switch
    def _step_kill_switch(
        self, intent: OrderIntent, st: PortfolioState, now: datetime
    ) -> Verdict | None:
        if not self.kill_switch.is_tripped():
            return None
        ks = self.kill_switch.state()
        reason = ks.reason.value if ks.reason else "?"
        return _reject(Reason.KILL_SWITCH, f"kill switch tripped: {reason} {ks.detail}")

    # 2. well-formed intent, then mode vs side
    def _step_side(self, intent: OrderIntent, st: PortfolioState, now: datetime) -> Verdict | None:
        qty, px = intent.qty, intent.ref_price
        problem = None
        if intent.side not in ("buy", "sell"):
            problem = f"unknown side {intent.side!r}"
        elif not (math.isfinite(qty) and qty > 0):
            problem = f"qty must be finite and > 0, got {qty}"
        elif not (math.isfinite(px) and px > 0):
            problem = f"ref_price must be finite and > 0, got {px}"
        elif not (_is_utc_aware(now) and _is_utc_aware(intent.bar_ts)):
            problem = "timestamps must be tz-aware UTC"
        if problem is not None:
            return _reject(Reason.INVALID_ORDER, problem)
        q_pre = float(st.positions.get(intent.symbol, 0.0))
        q_post = q_pre + _sign(intent) * qty
        opens_short = q_post < 0 and q_post < q_pre  # grows a short or crosses into one
        if opens_short and not self._shorts_ok:
            return _reject(
                Reason.SHORT_NOT_ALLOWED,
                f"post-fill qty {q_post:g} < 0; shorts allowed only in backtest with "
                f"allow_short (mode={self.mode.value}, allow_short={self.limits.allow_short})",
            )
        return None

    # 3. whitelist
    def _step_whitelist(
        self, intent: OrderIntent, st: PortfolioState, now: datetime
    ) -> Verdict | None:
        if intent.symbol in self._whitelist:
            return None
        return _reject(Reason.SYMBOL_NOT_ALLOWED, f"{intent.symbol} not whitelisted")

    # 4. freshness: age of the latest bar the decision used (a bar is identified by its open)
    def _step_fresh(self, intent: OrderIntent, st: PortfolioState, now: datetime) -> Verdict | None:
        age = now - intent.bar_ts
        if age < timedelta(0):
            return _reject(Reason.INVALID_ORDER, f"bar_ts {intent.bar_ts} is after now {now}")
        if age > self._stale:
            return _reject(Reason.STALE_DATA, f"latest bar is {age} old > {self._stale}")
        return None

    # 5. order rate over the trailing hour, inclusive of its start
    def _step_rate(self, intent: OrderIntent, st: PortfolioState, now: datetime) -> Verdict | None:
        cap = self.limits.max_orders_per_hour
        if cap is None:
            return None
        while self._approvals and self._approvals[0] < now - _HOUR:
            self._approvals.popleft()
        if len(self._approvals) >= cap:
            return _reject(Reason.ORDER_RATE, f"{len(self._approvals)} orders in the trailing hour")
        return None

    # 6. per-order notional: reject, never clip
    def _step_order_notional(
        self, intent: OrderIntent, st: PortfolioState, now: datetime
    ) -> Verdict | None:
        cap = self.limits.max_order_notional
        notional = intent.qty * intent.ref_price
        if cap is not None and notional > cap:
            return _reject(Reason.ORDER_NOTIONAL, f"order notional {notional:.2f} > {cap:.2f}")
        return None

    # 7-9: exposure clips, then the dust floor on what would actually be sent (math.md §5.1)
    def _step_exposure_and_dust(self, intent: OrderIntent, st: PortfolioState) -> Verdict:
        clip = self._clip_exposure(intent, st)
        if isinstance(clip, Verdict):
            return clip
        final, clipped = clip
        px = intent.ref_price
        if final * px < self.limits.min_order_notional:
            suffix = f" after clip ({'; '.join(clipped)})" if clipped else ""
            return _reject(
                Reason.BELOW_MIN_NOTIONAL,
                f"notional {final * px:.4f} < {self.limits.min_order_notional}{suffix}",
            )
        if clipped:
            return Verdict(True, Reason.CLIPPED, adjusted_qty=final, detail="; ".join(clipped))
        return Verdict(True, Reason.APPROVED)

    def _clip_exposure(
        self, intent: OrderIntent, st: PortfolioState
    ) -> Verdict | tuple[float, list[str]]:
        """Steps 7 and 8: clip (never enlarge) to the post-fill caps, or reject if no room."""
        lim = self.limits
        px, sign = intent.ref_price, _sign(intent)
        equity = st.equity
        if not (math.isfinite(equity) and equity > 0):
            return _reject(Reason.NON_POSITIVE_EQUITY, f"equity {equity}")
        q_pre = float(st.positions.get(intent.symbol, 0.0))
        q_post = q_pre + sign * intent.qty
        final = intent.qty
        clipped: list[str] = []
        # A risk-reducing order (|q| shrinks, no sign flip) is never clipped or blocked.
        if abs(q_post) <= abs(q_pre) and q_post * q_pre >= 0:
            return final, clipped

        # 7. |q_post| * px <= min(pct * V, notional cap)
        cap_notional = lim.max_position_pct_equity / 100.0 * equity
        if lim.max_position_notional is not None:
            cap_notional = min(cap_notional, lim.max_position_notional)
        room = cap_notional / px - sign * q_pre  # largest qty keeping |q_post| <= cap
        if room <= 0:
            return _reject(Reason.POSITION_LIMIT, f"already at cap {cap_notional:.2f}")
        if final > room:
            final = room
            clipped.append(f"position cap {cap_notional:.2f}")

        # 8. sum_j |q_j| C_j <= gross% * V, other symbols at their marks
        others = 0.0
        for sym, q in st.positions.items():
            if sym == intent.symbol or q == 0:
                continue
            mark = st.marks.get(sym)
            if mark is None or not (math.isfinite(mark) and mark > 0):
                return _reject(Reason.MISSING_MARK, f"no valid mark for held {sym}")
            others += abs(q) * mark
        gross_cap = lim.max_gross_exposure_pct / 100.0 * equity
        room = (gross_cap - others) / px - sign * q_pre
        if room <= 0:
            return _reject(Reason.GROSS_EXPOSURE, f"gross {others:.2f} >= cap {gross_cap:.2f}")
        if final > room:
            final = room
            clipped.append(f"gross cap {gross_cap:.2f}")
        return final, clipped

    # ----------------------------------------------------------------- marks
    def on_mark(self, equity: float, now: datetime) -> None:
        """Mark-to-market hook, called once per mark (risk-model.md §3).

        DAILY_LOSS:   V_now / V_anchor - 1 <= -max_daily_loss_pct, where V_anchor is the first
                      mark at/after 00:00 UTC of the current UTC day.
        MAX_DRAWDOWN: V_now / M - 1 <= -max_drawdown_pct, M = HWM since the last manual reset.
        """
        if not math.isfinite(equity):
            raise ValueError(f"equity must be finite, got {equity}")
        if not _is_utc_aware(now):
            raise ValueError("now must be tz-aware UTC")
        day = now.astimezone(UTC).date()
        ks = self.kill_switch
        m = ks.marks()
        anchor_day, anchor = m.anchor_day, m.anchor_equity
        if anchor_day != day or anchor is None:
            anchor_day, anchor = day, equity
        hwm = equity if m.hwm is None else max(m.hwm, equity)
        new = RiskMarks(hwm=hwm, anchor_day=anchor_day, anchor_equity=anchor)
        if new != m:
            ks.save_marks(new)

        if ks.is_tripped():
            return
        lim = self.limits
        if anchor <= 0 or equity / anchor - 1.0 <= -lim.max_daily_loss_pct / 100.0:
            ks.trip(
                TripReason.DAILY_LOSS,
                now,
                f"equity {equity:.2f} vs 00:00 UTC anchor {anchor:.2f} "
                f"(limit -{lim.max_daily_loss_pct}%)",
            )
        elif hwm <= 0 or equity / hwm - 1.0 <= -lim.max_drawdown_pct / 100.0:
            ks.trip(
                TripReason.MAX_DRAWDOWN,
                now,
                f"equity {equity:.2f} vs HWM {hwm:.2f} (limit -{lim.max_drawdown_pct}%)",
            )
