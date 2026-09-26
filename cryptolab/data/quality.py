"""OHLCV data-quality report: gaps, duplicates, bad values (FR-03).

Report only: nothing here modifies or forward-fills data. The P1 acceptance
criterion is < 1% missing bars (``QualityReport.passed()``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cryptolab.data.frame import timeframe_delta

Gap = tuple[pd.Timestamp, pd.Timestamp, int]  # (first missing open ts, last missing open ts, n)

_PRICES = ["open", "high", "low", "close"]
_ALL = [*_PRICES, "volume"]


@dataclass(frozen=True)
class QualityReport:
    timeframe: str
    n_bars: int  # distinct bar timestamps present
    expected_bars: int  # bars on the grid from first to last ts, inclusive
    missing_bars: int
    missing_pct: float  # 100 * missing / expected
    gaps: tuple[Gap, ...]
    duplicate_ts: int  # rows beyond the first for a repeated ts
    nan_rows: int
    non_positive_price_rows: int
    high_below_open_close: int  # high < max(open, close)
    low_above_open_close: int  # low > min(open, close)
    negative_volume: int
    bad_value_rows: int  # rows with at least one bad-value issue
    first_ts: pd.Timestamp | None = None
    last_ts: pd.Timestamp | None = None

    def passed(self, max_missing_pct: float = 1.0) -> bool:
        """P1: non-empty, missing < ``max_missing_pct`` %, no duplicate ts, no bad values."""
        return (
            self.n_bars > 0
            and self.missing_pct < max_missing_pct
            and self.duplicate_ts == 0
            and self.bad_value_rows == 0
        )

    def to_text(self, max_missing_pct: float = 1.0, max_gaps: int = 10) -> str:
        verdict = "PASS" if self.passed(max_missing_pct) else "FAIL"
        lines = [
            f"Quality [{self.timeframe}] {verdict} (threshold: < {max_missing_pct:.2f}% missing)",
            f"  range:        {self.first_ts} -> {self.last_ts}",
            f"  bars:         {self.n_bars} present / {self.expected_bars} expected",
            f"  missing:      {self.missing_bars} ({self.missing_pct:.2f}%)"
            f" in {len(self.gaps)} gaps",
            f"  duplicate ts: {self.duplicate_ts}",
            f"  bad rows:     {self.bad_value_rows} (NaN {self.nan_rows}, "
            f"price<=0 {self.non_positive_price_rows}, "
            f"high<max(o,c) {self.high_below_open_close}, "
            f"low>min(o,c) {self.low_above_open_close}, "
            f"volume<0 {self.negative_volume})",
        ]
        biggest = sorted(self.gaps, key=lambda g: g[2], reverse=True)[:max_gaps]
        for start, end, n in sorted(biggest):
            lines.append(f"  gap: {start:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M} ({n} bars)")
        if len(self.gaps) > max_gaps:
            lines.append(f"  ... {len(self.gaps) - max_gaps} smaller gaps not shown")
        return "\n".join(lines)


def quality_report(df: pd.DataFrame, timeframe: str) -> QualityReport:
    """Inspect an OHLCV frame (any order, duplicates allowed) without modifying it."""
    step = pd.Timedelta(timeframe_delta(timeframe))
    ts_all = pd.to_datetime(df["ts"], utc=True)
    ts = pd.Series(ts_all.drop_duplicates().sort_values().to_numpy())
    n_bars = len(ts)
    duplicate_ts = len(ts_all) - n_bars

    gaps: list[Gap] = []
    if n_bars:
        diffs = ts.diff()
        for i in np.flatnonzero((diffs > step).to_numpy()):
            n_missing = int(diffs.iloc[i] // step) - 1
            if n_missing <= 0:
                continue
            prev = pd.Timestamp(ts.iloc[i - 1])
            gaps.append((prev + step, prev + n_missing * step, n_missing))
    missing = sum(g[2] for g in gaps)
    expected = n_bars + missing

    vals = df[_ALL]
    oc_max = vals[["open", "close"]].max(axis=1, skipna=False)
    oc_min = vals[["open", "close"]].min(axis=1, skipna=False)
    nan_mask = vals.isna().any(axis=1)
    nonpos_mask = (vals[_PRICES] <= 0).any(axis=1)
    high_mask = vals["high"] < oc_max
    low_mask = vals["low"] > oc_min
    negvol_mask = vals["volume"] < 0
    bad_mask = nan_mask | nonpos_mask | high_mask | low_mask | negvol_mask

    return QualityReport(
        timeframe=timeframe,
        n_bars=n_bars,
        expected_bars=expected,
        missing_bars=missing,
        missing_pct=100.0 * missing / expected if expected else 0.0,
        gaps=tuple(gaps),
        duplicate_ts=duplicate_ts,
        nan_rows=int(nan_mask.sum()),
        non_positive_price_rows=int(nonpos_mask.sum()),
        high_below_open_close=int(high_mask.sum()),
        low_above_open_close=int(low_mask.sum()),
        negative_volume=int(negvol_mask.sum()),
        bad_value_rows=int(bad_mask.sum()),
        first_ts=pd.Timestamp(ts.iloc[0]) if n_bars else None,
        last_ts=pd.Timestamp(ts.iloc[-1]) if n_bars else None,
    )
