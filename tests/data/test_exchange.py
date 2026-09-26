"""FR-01/FR-04: public, rate-limited CCXT client; one real-network smoke test."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cryptolab.data.download import fetch_ohlcv_range
from cryptolab.data.exchange import make_exchange
from tests.data.conftest import assert_schema


def test_make_exchange_is_public_and_rate_limited() -> None:
    ex = make_exchange("binance")  # constructing the client does not hit the network
    assert ex.id == "binance"
    assert ex.enableRateLimit is True
    assert not ex.apiKey
    assert not ex.secret


def test_make_exchange_fallback_id_is_a_parameter() -> None:
    assert make_exchange("okx").id == "okx"


def test_make_exchange_rejects_unknown_id() -> None:
    with pytest.raises(ValueError, match="unknown"):
        make_exchange("not-an-exchange")


@pytest.mark.network
def test_real_binance_btc_hourly_bars() -> None:
    ex = make_exchange("binance")
    now = datetime.now(UTC)
    df = fetch_ohlcv_range(ex, "BTC/USDT", "1h", now - timedelta(hours=6))
    assert 4 <= len(df) <= 6
    assert_schema(df)
    assert (df["ts"] + timedelta(hours=1) <= now).all()
