"""Buy and hold (FR-09): buy target weights once, then hold (never rebalance)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from cryptolab.engine.types import MarketView


class BuyAndHold:
    """Equal weight across ``symbols`` (all of the view's symbols if None), or explicit weights.

    The weights are emitted once, at the first decision where every target symbol has at
    least one completed bar, so the buy fills at the next open (math.md §3.7: "buys at O_1").
    Afterwards the strategy returns ``{}``, which keeps positions unchanged. It is stateless:
    the emit bar is derived from the visible bars alone, so one instance can be reused.
    """

    name = "buy_and_hold"
    summary = "Buy once at the start and hold."

    def __init__(
        self,
        symbols: Sequence[str] | None = None,
        weights: Mapping[str, float] | None = None,
    ) -> None:
        if symbols is not None and weights is not None:
            raise ValueError("pass symbols or weights, not both")
        if symbols is not None and len(symbols) == 0:
            raise ValueError("symbols must not be empty")
        if weights is not None:
            if not weights:
                raise ValueError("weights must not be empty")
            if not all(math.isfinite(w) and w >= 0 for w in weights.values()):
                raise ValueError("weights must be finite and >= 0 (long-only)")
            if sum(weights.values()) > 1 + 1e-12:
                raise ValueError("weights must sum to <= 1 (no leverage)")
        self.symbols = None if symbols is None else tuple(symbols)
        self.weights = None if weights is None else dict(weights)

    def _targets(self, view: MarketView) -> dict[str, float]:
        if self.weights is not None:
            return dict(self.weights)
        syms = self.symbols if self.symbols is not None else view.symbols
        return {s: 1.0 / len(syms) for s in syms}

    def on_bar(self, view: MarketView) -> dict[str, float]:
        targets = self._targets(view)
        counts = [len(view.bars(s, 2)) for s in targets]
        # Emit exactly when the latest-starting symbol has its first completed bar.
        if min(counts) == 1:
            return targets
        return {}
