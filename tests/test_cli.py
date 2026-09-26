from typer.testing import CliRunner

from cryptolab.cli import app


def test_status_reports_paper_by_default(monkeypatch):
    monkeypatch.delenv("CRYPTOLAB_MODE", raising=False)
    monkeypatch.chdir("tests")  # no .env here
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Mode: paper" in result.output
