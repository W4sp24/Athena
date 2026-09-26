"""Value types passed between the engine/runner and RiskGate (architecture.md §3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

# Same values as cryptolab.engine.types.Side; redeclared so risk never imports the engine
# package (the engine imports risk, so the reverse would be an import cycle).
Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class OrderIntent:
    """An order the engine/runner wants to place. Must pass RiskGate before it can fill.

    ``qty`` is in base units and always > 0; direction is ``side``. ``ref_price`` is the
    price used for sizing and limit checks (the close at decision time, math.md §5.1).
    ``bar_ts`` is the open time of the latest bar of ``symbol`` the decision used.
    """

    strategy: str
    symbol: str
    side: Side
    qty: float
    ref_price: float
    decision_ts: datetime
    bar_ts: datetime
    signal_inputs: Mapping[str, Any] = field(default_factory=dict)


class Reason(StrEnum):
    """Verdict reason codes (risk-model.md §2)."""

    APPROVED = "approved"
    CLIPPED = "clipped"
    KILL_SWITCH = "KILL_SWITCH"
    INVALID_ORDER = "INVALID_ORDER"
    SHORT_NOT_ALLOWED = "SHORT_NOT_ALLOWED"
    SYMBOL_NOT_ALLOWED = "SYMBOL_NOT_ALLOWED"
    STALE_DATA = "STALE_DATA"
    ORDER_RATE = "ORDER_RATE"
    ORDER_NOTIONAL = "ORDER_NOTIONAL"
    NON_POSITIVE_EQUITY = "NON_POSITIVE_EQUITY"
    MISSING_MARK = "MISSING_MARK"
    POSITION_LIMIT = "POSITION_LIMIT"
    GROSS_EXPOSURE = "GROSS_EXPOSURE"
    BELOW_MIN_NOTIONAL = "BELOW_MIN_NOTIONAL"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class Verdict:
    """RiskGate outcome. ``adjusted_qty`` is set only when an approved order was clipped."""

    approved: bool
    reason: Reason
    adjusted_qty: float | None = None
    detail: str = ""

    def final_qty(self, intent: OrderIntent) -> float:
        """Quantity that may be sent: the clipped qty if clipped, else the intent's, else 0."""
        if not self.approved:
            return 0.0
        return intent.qty if self.adjusted_qty is None else self.adjusted_qty


@dataclass(frozen=True)
class PortfolioState:
    """Snapshot RiskGate checks against. ``positions`` are signed base quantities."""

    cash: float
    positions: Mapping[str, float]
    marks: Mapping[str, float]
    equity: float
