"""FR-03: gap / duplicate / bad-value detection on synthetic frames with known defects."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from cryptolab.data.quality import QualityReport, quality_report
from tests.data.conftest import T0, make_frame

H = timedelta(hours=1)


def test_clean_frame_passes() -> None:
    rep = quality_report(make_frame(n_bars=100), "1h")
    assert rep.n_bars == 100
    assert rep.expected_bars == 100
    assert rep.missing_bars == 0
    assert rep.missing_pct == 0.0
    assert rep.gaps == ()
    assert rep.duplicate_ts == 0
    assert rep.bad_value_rows == 0
    assert rep.passed()


def test_detects_gaps_with_bounds_and_counts() -> None:
    df = make_frame(n_bars=100, skip={10, 11, 12, 50})
    rep = quality_report(df, "1h")
    assert rep.n_bars == 96
    assert rep.expected_bars == 100
    assert rep.missing_bars == 4
    assert rep.missing_pct == pytest.approx(4.0)
    assert rep.gaps == (
        (pd.Timestamp(T0 + 10 * H), pd.Timestamp(T0 + 12 * H), 3),
        (pd.Timestamp(T0 + 50 * H), pd.Timestamp(T0 + 50 * H), 1),
    )
    assert not rep.passed()
    assert rep.passed(max_missing_pct=5.0)


def test_p1_threshold_is_strictly_below_one_percent() -> None:
    assert quality_report(make_frame(n_bars=200, skip={100}), "1h").passed()  # 0.5%
    assert not quality_report(make_frame(n_bars=100, skip={50}), "1h").passed()  # 1.0%


def test_counts_duplicate_timestamps() -> None:
    df = make_frame(n_bars=10)
    dup = pd.concat([df, df.iloc[[3, 3, 7]]], ignore_index=True)
    rep = quality_report(dup, "1h")
    assert rep.duplicate_ts == 3
    assert rep.n_bars == 10
    assert rep.missing_bars == 0
    assert not rep.passed()


def test_unsorted_input_is_handled() -> None:
    df = make_frame(n_bars=10, skip={4}).iloc[::-1]
    rep = quality_report(df, "1h")
    assert rep.missing_bars == 1
    assert rep.gaps == ((pd.Timestamp(T0 + 4 * H), pd.Timestamp(T0 + 4 * H), 1),)


def test_detects_bad_values() -> None:
    df = make_frame(n_bars=10)
    df.loc[0, "close"] = np.nan
    df.loc[1, ["open", "low"]] = 0.0
    df.loc[2, "low"] = -5.0
    df.loc[3, "high"] = df.loc[3, "close"] - 0.5  # high below close
    df.loc[4, "low"] = df.loc[4, "open"] + 0.5  # low above open
    df.loc[5, "volume"] = -1.0
    rep = quality_report(df, "1h")
    assert rep.nan_rows == 1
    assert rep.non_positive_price_rows == 2
    assert rep.high_below_open_close == 1
    assert rep.low_above_open_close == 1
    assert rep.negative_volume == 1
    assert rep.bad_value_rows == 6
    assert rep.missing_bars == 0
    assert not rep.passed()


def test_does_not_modify_input() -> None:
    df = make_frame(n_bars=10, skip={3})
    snapshot = df.copy()
    quality_report(df, "1h")
    pd.testing.assert_frame_equal(df, snapshot)


def test_empty_frame() -> None:
    rep = quality_report(make_frame(n_bars=0), "1h")
    assert rep.n_bars == 0
    assert rep.expected_bars == 0
    assert rep.missing_pct == 0.0
    assert not rep.passed()


def test_other_timeframe_step() -> None:
    rep = quality_report(make_frame(n_bars=20, timeframe="4h", skip={5}), "4h")
    assert rep.expected_bars == 20
    assert rep.missing_bars == 1


def test_report_is_frozen_and_has_text_summary() -> None:
    rep = quality_report(make_frame(n_bars=100, skip={10}), "1h")
    assert isinstance(rep, QualityReport)
    with pytest.raises(AttributeError):
        rep.n_bars = 5  # type: ignore[misc]
    text = rep.to_text()
    assert "missing" in text.lower()
    assert "1.00%" in text
    assert "FAIL" in text
    assert "2024-01-01 10:00" in text
