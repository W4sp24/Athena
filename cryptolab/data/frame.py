"""Helpers that build and normalise frames to the OHLCV contract in ``schema.py``."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import pandas as pd

from cryptolab.data.schema import OHLCV_COLUMNS, TIMEFRAME_SECONDS

TS_DTYPE = pd.DatetimeTZDtype(unit="ns", tz="UTC")
_PRICE_VOL = list(OHLCV_COLUMNS[1:])


def timeframe_seconds(timeframe: str) -> int:
    """Bar length in seconds; raises ``ValueError`` for timeframes outside the schema."""
    try:
        return TIMEFRAME_SECONDS[timeframe]
    except KeyError:
        known = ", ".join(TIMEFRAME_SECONDS)
        raise ValueError(f"unsupported timeframe {timeframe!r}; expected one of {known}") from None


def timeframe_delta(timeframe: str) -> timedelta:
    return timedelta(seconds=timeframe_seconds(timeframe))


def require_utc(dt: datetime, name: str) -> datetime:
    """Reject naive datetimes (all timestamps in CryptoLab are tz-aware UTC)."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware (UTC), got naive {dt!r}")
    return dt


def empty_ohlcv() -> pd.DataFrame:
    """Zero-row frame with the contract's columns and dtypes."""
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV_COLUMNS})
    df["ts"] = pd.Series(dtype=TS_DTYPE)
    return df[list(OHLCV_COLUMNS)]


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce dtypes, sort by ``ts``, and drop duplicate ``ts`` (last occurrence wins)."""
    if df.empty:
        return empty_ohlcv()
    out = df.loc[:, list(OHLCV_COLUMNS)].copy()
    ts = pd.to_datetime(out["ts"], utc=True)
    out["ts"] = ts.astype(TS_DTYPE)
    out[_PRICE_VOL] = out[_PRICE_VOL].astype("float64")
    out = out.drop_duplicates(subset="ts", keep="last")
    out = out.sort_values("ts", kind="stable").reset_index(drop=True)
    return out


def from_ccxt_rows(rows: Sequence[Sequence[float]]) -> pd.DataFrame:
    """Convert CCXT ``[ms, o, h, l, c, v]`` rows into a normalised OHLCV frame."""
    if not rows:
        return empty_ohlcv()
    df = pd.DataFrame([list(r[:6]) for r in rows], columns=list(OHLCV_COLUMNS))
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    return normalize_ohlcv(df)
