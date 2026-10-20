from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    real = Path.home() / ".agent-lb/state/browser-profiles.json"
    before = real.stat().st_mtime_ns if real.exists() else None
    with tempfile.TemporaryDirectory(prefix="agent-lb-profiles-") as temporary:
        root = Path(temporary)
        env = {
            **os.environ,
            "AGENT_LB_STATE_DIR": str(root / "state"),
            "AGENT_LB_CHROME_ROOT": str(root),
            "AGENT_LB_CHROME": "/usr/bin/false",
            "AGENT_LB_BASE_URL": "http://127.0.0.1:1",
        }

        def run(*args: str, rc: int = 0) -> subprocess.CompletedProcess[str]:
            result = subprocess.run(
                ["uv", "run", "agent-lb", *args], env=env, capture_output=True, text=True, timeout=30
            )
            assert result.returncode == rc, f"{args}: expected {rc}, got {result.returncode}: {result.stderr}"
            return result

        run("profiles", "add", "afn9@case.edu", "--dir", "afn9@case.edu", "--provider", "anthropic")
        run("profiles", "add", "alex@chordtalent.com", "--dir", "a.neyman17+lb3@gmail.com", "--provider", "anthropic")
        rows = {row["email"]: row for row in json.loads(run("profiles", "list", "--json").stdout)}
        assert len(rows) == 2
        assert rows["afn9@case.edu"]["cdpPort"] == 9333
        assert rows["afn9@case.edu"]["dir"] == "afn9@case.edu"
        assert rows["alex@chordtalent.com"]["cdpPort"] == 9334
        assert rows["alex@chordtalent.com"]["dir"] == "a.neyman17+lb3@gmail.com"
        assert all(not row["exists"] for row in rows.values())
        run("profiles", "add", "AFN9@CASE.EDU", "--provider", "openai")
        rows = {row["email"]: row for row in json.loads(run("profiles", "list", "--json").stdout)}
        assert rows["afn9@case.edu"]["cdpPort"] == 9333
        assert rows["afn9@case.edu"]["providers"] == ["anthropic", "openai"]
        plan = json.loads(run("reauth", "alex@chordtalent.com", "--dry-run").stdout)
        assert f"--user-data-dir={root}/a.neyman17+lb3@gmail.com" in plan["chrome_argv"]
        assert "--remote-debugging-port=9334" in plan["chrome_argv"]
        assert "--remote-debugging-address=127.0.0.1" in plan["chrome_argv"]
        assert plan["start"]["body"] == {"provider": "anthropic", "forceMethod": "browser"}
        assert plan["complete"] == "manual-callback via CDP"
        assert "profiles add" in run("reauth", "nobody@x", "--dry-run", rc=2).stderr
        registry = root / "state/browser-profiles.json"
        assert stat.S_IMODE(registry.stat().st_mode) == 0o600
        assert not (root / "afn9@case.edu").exists()
        registry.write_text('{"version":1,"profiles":[]}')
        assert json.loads(run("profiles", "list", "--json").stdout) == []
        registry.write_text("{")
        assert json.loads(run("profiles", "list", "--json").stdout) == []
    after = real.stat().st_mtime_ns if real.exists() else None
    assert before == after, "real profile registry changed"
    print("browser profiles: subprocess checks passed; real registry unchanged")


if __name__ == "__main__":
    main()
