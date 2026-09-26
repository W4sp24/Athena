"""Time-series momentum (FR-11): hold the symbols whose trailing return beats a threshold."""

from __future__ import annotations

import math
from collections.abc import Sequence

from cryptolab.engine.types import MarketView


class Momentum:
    """Trailing return ``C_t / C_{t-lookback} - 1`` per symbol (over completed bars).

    Symbols with return > ``threshold`` share the book equally; the others get 0. Long-only.
    A symbol with fewer than ``lookback + 1`` bars is omitted (no opinion).
    """

    name = "momentum"

    def __init__(
        self, lookback: int, threshold: float = 0.0, symbols: Sequence[str] | None = None
    ) -> None:
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        if not math.isfinite(threshold):
            raise ValueError("threshold must be finite")
        self.lookback = int(lookback)
        self.threshold = float(threshold)
        self.symbols = None if symbols is None else tuple(symbols)

    def on_bar(self, view: MarketView) -> dict[str, float]:
        syms = self.symbols if self.symbols is not None else view.symbols
        returns: dict[str, float] = {}
        for s in syms:
            closes = view.bars(s, self.lookback + 1)["close"].to_numpy()
            if len(closes) < self.lookback + 1:
                continue
            returns[s] = float(closes[-1] / closes[0] - 1.0)
        winners = [s for s, r in returns.items() if r > self.threshold]
        return {s: (1.0 / len(winners) if s in winners else 0.0) for s in returns}
