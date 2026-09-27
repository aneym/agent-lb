from __future__ import annotations

import argparse
import runpy
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("status", [401, 403])
def test_operator_denial_explains_dashboard_and_proxy_locality(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    cli = runpy.run_path(str(Path(__file__).resolve().parents[2] / "clients" / "agent-lb-federation"))
    command = cli["cmd_status"]
    monkeypatch.setitem(command.__globals__, "_authenticate", lambda local: True)
    monkeypatch.setitem(
        command.__globals__,
        "_http",
        lambda *args, **kwargs: cli["HttpResult"](
            ok=False, status=status, body={"detail": "sensitive-response-marker"}, error="sensitive-response-marker"
        ),
    )

    assert command(argparse.Namespace()) != 0
    output = capsys.readouterr()
    assert "dashboard session" in output.err
    assert "local request" in output.err
    assert "does not trust proxy headers" in output.err
    assert "sensitive-response-marker" not in output.err + output.out
