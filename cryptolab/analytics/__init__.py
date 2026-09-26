"""Metrics per docs/sdd/math.md and the BTC buy-and-hold benchmark (FR-17..FR-20)."""

from cryptolab.analytics.metrics import (
    FifoResult,
    Metrics,
    RoundTrip,
    compute_metrics,
    drawdown_series,
    fifo_round_trips,
    periods_per_year,
)
from cryptolab.analytics.report import Report, build_report, equity_frame

__all__ = [
    "FifoResult",
    "Metrics",
    "Report",
    "RoundTrip",
    "build_report",
    "compute_metrics",
    "drawdown_series",
    "equity_frame",
    "fifo_round_trips",
    "periods_per_year",
]
