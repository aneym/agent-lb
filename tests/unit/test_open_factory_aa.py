"""`open-factory aa-sync`: Artificial Analysis snapshots as routing evidence, key never exposed."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "clients" / "open-factory" / "bin" / "open-factory"
FIXTURE = REPO / "tests" / "fixtures" / "aa" / "models.json"
KEY = "sentinel-aa-key-3f9c"

sys.path.insert(0, str(REPO / "clients" / "open-factory"))
from open_factory import aa, cli  # noqa: E402


def test_dry_run_maps_fixture_slugs_without_key_or_writes(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "ARTIFICIAL_ANALYSIS_API_KEY"}
    env["OF_AA_DIR"] = str(tmp_path / "aa")
    out = subprocess.run(
        [sys.executable, str(CLI), "aa-sync", "--dry-run", "--json"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(out.stdout)
    assert [row["slug"] for row in result["opus"]] == ["claude-opus-5-5-high"]
    assert result["opus"][0]["variant"] == "high"
    assert result["opus"][0]["coding_index"] == 77.2
    assert [row["slug"] for row in result["grok-latest"]] == ["grok-4-7"]
    assert result["swe-latest"] == []
    assert result["unmapped"] == ["example-open-model-2", "unmapped-example-model"]
    assert not (tmp_path / "aa").exists()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Serve the fixture as AA's API, recording the key each call sent."""
    sent: list[str] = []

    def urlopen(request, timeout):  # noqa: ANN001
        sent.append(request.get_header("X-api-key"))
        return io.BytesIO(FIXTURE.read_bytes())

    monkeypatch.setattr(aa.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("OF_AA_DIR", str(tmp_path))
    monkeypatch.setenv("ARTIFICIAL_ANALYSIS_API_KEY", KEY)
    return sent


def test_fetches_once_a_day_and_never_writes_or_prints_the_key(
    served: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["aa-sync"]) == 0
    printed = [capsys.readouterr()]
    assert cli.main(["aa-sync", "--json"]) == 0
    printed.append(capsys.readouterr())
    assert json.loads(printed[-1].out)["skipped"] is True
    assert len(served) == 1

    assert cli.main(["aa-sync", "--force"]) == 0
    printed.append(capsys.readouterr())
    assert served == [KEY, KEY]
    snapshots = sorted(tmp_path.glob("[0-9]*.json"))
    assert len(snapshots) == 2
    assert json.loads((tmp_path / "latest.json").read_text()) == json.loads(snapshots[-1].read_text())
    texts = [t for c in printed for t in (c.out, c.err)] + [p.read_text() for p in tmp_path.iterdir()]
    assert all(KEY not in text for text in texts)


def test_missing_key_fails_before_any_request(served: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARTIFICIAL_ANALYSIS_API_KEY")
    with pytest.raises(SystemExit, match="ARTIFICIAL_ANALYSIS_API_KEY is not set"):
        cli.main(["aa-sync"])
    assert served == []


@pytest.mark.parametrize(("age_days", "fresh"), [(1, True), (9, False)])
def test_evidence_is_withheld_once_the_snapshot_is_eight_days_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age_days: int, fresh: bool
) -> None:
    monkeypatch.setenv("OF_AA_DIR", str(tmp_path))
    snapshot = json.loads(FIXTURE.read_text())
    fetched = datetime.now(timezone.utc) - timedelta(days=age_days)
    snapshot["fetched_at"] = fetched.isoformat().replace("+00:00", "Z")
    (tmp_path / "latest.json").write_text(json.dumps(snapshot))
    rows = aa.evidence("sol-latest")
    assert (rows is not None) is fresh
    if fresh:
        assert [row["slug"] for row in rows] == ["gpt-6-sol-high"]
