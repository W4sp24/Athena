"""Moving-average crossover (FR-10): long when the fast SMA is above the slow SMA."""

from __future__ import annotations

from collections.abc import Sequence

from cryptolab.engine.types import MarketView


class MACrossover:
    """Per symbol: weight ``1 / n_symbols`` when SMA(fast) > SMA(slow) of closes, else 0.

    Long-only. A symbol with fewer than ``slow`` completed bars is omitted (no opinion, so
    its position is kept; it starts flat).
    """

    name = "ma_crossover"

    def __init__(self, fast: int, slow: int, symbols: Sequence[str] | None = None) -> None:
        if not (1 <= fast < slow):
            raise ValueError(f"need 1 <= fast < slow, got fast={fast}, slow={slow}")
        self.fast = int(fast)
        self.slow = int(slow)
        self.symbols = None if symbols is None else tuple(symbols)

    def on_bar(self, view: MarketView) -> dict[str, float]:
        syms = self.symbols if self.symbols is not None else view.symbols
        w = 1.0 / len(syms)
        out: dict[str, float] = {}
        for s in syms:
            closes = view.bars(s, self.slow)["close"].to_numpy()
            if len(closes) < self.slow:
                continue
            fast_sma = float(closes[-self.fast :].mean())
            slow_sma = float(closes.mean())
            out[s] = w if fast_sma > slow_sma else 0.0
        return out
