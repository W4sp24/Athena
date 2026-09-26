"""Shared builders for engine tests."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from cryptolab.config import RiskLimits, load_risk_limits
from cryptolab.engine.types import MarketView

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def research_limits(**over: Any) -> RiskLimits:
    base = load_risk_limits(ROOT / "configs" / "risk.backtest.yaml")
    return RiskLimits.model_validate(base.model_dump() | over)


def paper_limits(**over: Any) -> RiskLimits:
    base = load_risk_limits(ROOT / "configs" / "risk.paper.yaml")
    return RiskLimits.model_validate(base.model_dump() | over)


def make_bars(
    opens: Sequence[float],
    closes: Sequence[float] | None = None,
    *,
    start: datetime = T0,
    freq: str = "h",
    drop: Sequence[int] = (),
) -> pd.DataFrame:
    """OHLCV frame on a regular grid; ``drop`` removes rows by grid position."""
    o = np.asarray(opens, dtype=float)
    c = o.copy() if closes is None else np.asarray(closes, dtype=float)
    df = pd.DataFrame(
        {
            "ts": pd.date_range(start, periods=len(o), freq=freq, tz="UTC"),
            "open": o,
            "high": np.maximum(o, c) * 1.01,
            "low": np.minimum(o, c) * 0.99,
            "close": c,
            "volume": np.ones(len(o)),
        }
    )
    if drop:
        df = df.drop(index=list(drop)).reset_index(drop=True)
    return df


class Script:
    """Strategy returning scripted weights keyed by decision index (bars seen - 1 of ``clock``)."""

    name = "script"

    def __init__(
        self,
        plan: dict[int, dict[str, float]] | Callable[[int, MarketView], dict[str, float]],
        clock: str | None = None,
    ) -> None:
        self.plan = plan
        self.clock = clock
        self.calls: list[datetime] = []

    def on_bar(self, view: MarketView) -> dict[str, float]:
        self.calls.append(view.now)
        t = len(self.calls) - 1
        if callable(self.plan):
            return self.plan(t, view)
        return dict(self.plan.get(t, {}))
