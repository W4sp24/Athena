"""Parquet OHLCV store with a DuckDB query layer (FR-02, FR-03; architecture.md §4).

Layout::

    {root}/ohlcv/{exchange}/{timeframe}/{SYMBOL-SANITIZED}/year=YYYY/part.parquet

Parquet is the source of truth; the DuckDB view is rebuilt from it on connect.
Symbols are sanitised for paths by mapping ``/`` -> ``-`` and ``:`` -> ``_``
(``BTC/USDT`` -> ``BTC-USDT``), so symbols must not already contain ``-`` or ``_``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

from cryptolab.data.frame import empty_ohlcv, normalize_ohlcv, require_utc

OHLCV_DIR = "ohlcv"
PART_FILE = "part.parquet"
_YEAR_PREFIX = "year="


def sanitize_symbol(symbol: str) -> str:
    """``BTC/USDT`` -> ``BTC-USDT``; ``BTC/USDT:USDT`` -> ``BTC-USDT_USDT``."""
    return symbol.replace("/", "-").replace(":", "_")


def unsanitize_symbol(name: str) -> str:
    """Inverse of :func:`sanitize_symbol`."""
    return name.replace("-", "/").replace("_", ":")


class OhlcvStore:
    """Year-partitioned Parquet files, one series per (exchange, timeframe, symbol)."""

    def __init__(self, root: Path = Path("data")) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------ paths
    def series_dir(self, exchange: str, symbol: str, timeframe: str) -> Path:
        return self.root / OHLCV_DIR / exchange / timeframe / sanitize_symbol(symbol)

    def _year_files(self, exchange: str, symbol: str, timeframe: str) -> dict[int, Path]:
        base = self.series_dir(exchange, symbol, timeframe)
        out: dict[int, Path] = {}
        if not base.is_dir():
            return out
        for d in base.iterdir():
            f = d / PART_FILE
            if d.name.startswith(_YEAR_PREFIX) and f.is_file():
                out[int(d.name.removeprefix(_YEAR_PREFIX))] = f
        return dict(sorted(out.items()))

    @staticmethod
    def _read_file(path: Path) -> pd.DataFrame:
        return normalize_ohlcv(pd.read_parquet(path))

    # ------------------------------------------------------------------ write
    def write(self, exchange: str, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
        """Merge ``df`` into the stored series; returns the number of new bars added.

        Duplicate ``ts`` resolve in favour of ``df`` (new data wins). A partition
        whose merged content equals what is on disk is not rewritten, so writing
        the same data twice changes nothing.
        """
        new = normalize_ohlcv(df)
        if new.empty:
            return 0
        existing_files = self._year_files(exchange, symbol, timeframe)
        base = self.series_dir(exchange, symbol, timeframe)
        added = 0
        years = new["ts"].dt.year
        for year, part in new.groupby(years, sort=True):
            path = base / f"{_YEAR_PREFIX}{year}" / PART_FILE
            old = self._read_file(path) if int(year) in existing_files else empty_ohlcv()
            merged = normalize_ohlcv(pd.concat([old, part], ignore_index=True))
            added += len(merged) - len(old)
            if merged.equals(old):
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".parquet.tmp")
            merged.to_parquet(tmp, index=False)
            os.replace(tmp, path)
        return added

    # ------------------------------------------------------------------- read
    def read(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """Bars with ``start <= ts < end`` (either bound optional), per the OHLCV schema."""
        if start is not None:
            require_utc(start, "start")
        if end is not None:
            require_utc(end, "end")
        files = self._year_files(exchange, symbol, timeframe)
        frames = [
            self._read_file(f)
            for year, f in files.items()
            if (start is None or year >= start.astimezone(UTC).year)
            and (end is None or year <= end.astimezone(UTC).year)
        ]
        if not frames:
            return empty_ohlcv()
        df = normalize_ohlcv(pd.concat(frames, ignore_index=True))
        mask = pd.Series(True, index=df.index)
        if start is not None:
            mask &= df["ts"] >= pd.Timestamp(start)
        if end is not None:
            mask &= df["ts"] < pd.Timestamp(end)
        return df.loc[mask].reset_index(drop=True)

    def last_ts(self, exchange: str, symbol: str, timeframe: str) -> datetime | None:
        """Open time of the latest stored bar, or ``None`` if the series is empty."""
        files = self._year_files(exchange, symbol, timeframe)
        for f in reversed(files.values()):
            df = self._read_file(f)
            if not df.empty:
                last: datetime = df["ts"].iloc[-1].to_pydatetime()
                return last
        return None

    def list_series(self) -> list[tuple[str, str, str]]:
        """All stored series as sorted ``(exchange, timeframe, symbol)`` tuples."""
        base = self.root / OHLCV_DIR
        if not base.is_dir():
            return []
        found: set[tuple[str, str, str]] = set()
        for f in base.glob(f"*/*/*/{_YEAR_PREFIX}*/{PART_FILE}"):
            sym_dir = f.parent.parent
            tf_dir = sym_dir.parent
            found.add((tf_dir.parent.name, tf_dir.name, unsanitize_symbol(sym_dir.name)))
        return sorted(found)


_EMPTY_VIEW = """
CREATE OR REPLACE VIEW ohlcv AS
SELECT NULL::VARCHAR AS exchange, NULL::VARCHAR AS timeframe, NULL::VARCHAR AS symbol,
       NULL::TIMESTAMPTZ AS ts, NULL::DOUBLE AS open, NULL::DOUBLE AS high,
       NULL::DOUBLE AS low, NULL::DOUBLE AS close, NULL::DOUBLE AS volume
WHERE false
"""

_PATH_RE = r"/ohlcv/([^/]+)/([^/]+)/([^/]+)/year=[0-9]+/part\.parquet$"


def duckdb_connect(
    root: Path = Path("data"), database: str | Path = ":memory:"
) -> duckdb.DuckDBPyConnection:
    """Open DuckDB with a view ``ohlcv`` over every stored Parquet partition.

    Columns: ``exchange, timeframe, symbol, ts, open, high, low, close, volume``;
    the first three are parsed from the file path. The view is rebuilt on each
    call, so it always reflects the Parquet source of truth. The session time
    zone is UTC so ``ts`` comes back UTC (e.g. via ``.df()``); note DuckDB's
    ``fetchall()`` on TIMESTAMPTZ columns needs the ``pytz`` package.
    """
    con = duckdb.connect(str(database))
    con.execute("SET TimeZone = 'UTC'")
    base = Path(root) / OHLCV_DIR
    if not any(base.glob(f"*/*/*/{_YEAR_PREFIX}*/{PART_FILE}")):
        con.execute(_EMPTY_VIEW)
        return con
    pattern = (base.resolve() / "*" / "*" / "*" / f"{_YEAR_PREFIX}*" / PART_FILE).as_posix()
    pattern_sql = pattern.replace("'", "''")
    fname = "replace(filename, '\\', '/')"
    con.execute(
        f"""
        CREATE OR REPLACE VIEW ohlcv AS
        SELECT regexp_extract({fname}, '{_PATH_RE}', 1) AS exchange,
               regexp_extract({fname}, '{_PATH_RE}', 2) AS timeframe,
               replace(replace(regexp_extract({fname}, '{_PATH_RE}', 3), '-', '/'), '_', ':')
                   AS symbol,
               ts, open, high, low, close, volume
        FROM read_parquet('{pattern_sql}', filename = true, hive_partitioning = false)
        """  # noqa: S608 - path comes from the local store root, not user input
    )
    return con
