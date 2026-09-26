from pathlib import Path

from typer.testing import CliRunner

from cryptolab.cli import app
from cryptolab.data.store import OhlcvStore
from tests.data.conftest import make_frame


def _seed(root: Path, *, skip: tuple[int, ...] = ()) -> None:
    df = make_frame(n_bars=48, skip=skip)
    OhlcvStore(root).write("binance", "BTC/USDT", "1h", df)


def test_data_list_and_quality_pass(tmp_path: Path) -> None:
    _seed(tmp_path)
    runner = CliRunner()
    listed = runner.invoke(app, ["data", "list", "--root", str(tmp_path)])
    assert listed.exit_code == 0, listed.output
    assert "BTC/USDT" in listed.output
    q = runner.invoke(app, ["data", "quality", "BTC/USDT", "--root", str(tmp_path)])
    assert q.exit_code == 0, q.output


def test_data_quality_fails_loudly_on_gaps(tmp_path: Path) -> None:
    _seed(tmp_path, skip=(5, 6, 7))  # 3/48 missing > 1% -> P1 criterion fails
    q = CliRunner().invoke(app, ["data", "quality", "BTC/USDT", "--root", str(tmp_path)])
    assert q.exit_code == 1
