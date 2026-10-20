from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


def main() -> None:
    real = Path.home() / ".agent-lb/state/subscriptions.json"
    before = (real.read_bytes(), real.stat().st_mtime_ns) if real.exists() else None
    with tempfile.TemporaryDirectory(prefix="agent-lb-subscriptions-") as temporary:
        root = Path(temporary)
        env = {**os.environ, "AGENT_LB_STATE_DIR": str(root), "UV_NO_SYNC": "1"}

        def run(*args: str, rc: int = 0) -> subprocess.CompletedProcess[str]:
            result = subprocess.run(
                ["uv", "run", "agent-lb", "subscriptions", *args], env=env, capture_output=True, text=True, timeout=120
            )
            assert result.returncode == rc, f"{args}: expected {rc}, got {result.returncode}: {result.stderr}"
            return result

        assert json.loads(run("list", "--json").stdout) == []
        run(
            "set",
            "PAID@EXAMPLE.TEST",
            "--plan",
            "Pro",
            "--renews-on",
            "2026-11-01",
            "--payment",
            "single-use",
            "--amount",
            "20",
            "--note",
            "Replace card",
        )
        row = json.loads(run("list", "--json").stdout)[0]
        assert row == {
            "email": "paid@example.test",
            "plan": "Pro",
            "renewsOn": "2026-11-01",
            "payment": "single-use",
            "amountUsd": 20,
            "note": "Replace card",
        }
        assert "paid@example.test" in run("list").stdout
        run("set", "paid@example.test", "--payment", "card", "--renews-on", "2026-12-01")
        row = json.loads(run("list", "--json").stdout)[0]
        assert row["amountUsd"] == 20 and row["note"] == "Replace card" and row["plan"] == "Pro"
        assert row["payment"] == "card" and row["renewsOn"] == "2026-12-01"
        registry = root / "subscriptions.json"
        assert stat.S_IMODE(registry.stat().st_mode) == 0o600
        valid = registry.read_bytes()
        for flag, value in (
            ("--renews-on", "2026-02-30"),
            ("--renews-on", "20261101"),
            ("--payment", "cash"),
            ("--amount", "nan"),
            ("--amount", "-1"),
        ):
            assert run("set", "paid@example.test", flag, value, rc=2).stderr
            assert registry.read_bytes() == valid
        run("set", "new@example.test", "--payment", "card", rc=2)
        today = datetime.now(UTC).date()
        for payment, offset, expected in (
            ("single-use", 7, "renewal_payment"),
            ("none", -1, "renewal_payment"),
            ("single-use", 8, None),
            ("card", -2, None),
            ("card", -3, "renewal_stale"),
            ("card", 7, None),
        ):
            renewal = (today + timedelta(days=offset)).isoformat()
            run("set", "paid@example.test", "--renews-on", renewal, "--payment", payment)
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "python",
                    "-c",
                    "from app.db.models import Account, AccountStatus; from app.modules.accounts.health import account_problems; import json; a = Account(email='paid@example.test', status=AccountStatus.ACTIVE); print(json.dumps([p.model_dump() for p in account_problems(a, None, None)]))",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert result.returncode == 0, result.stderr
            problems = json.loads(result.stdout)
            assert [p["code"] for p in problems] == ([expected] if expected else [])
            if expected:
                assert (
                    renewal in problems[0]["message"]
                    and "agent-lb subscriptions set paid@example.test" in problems[0]["message"]
                )
        run("clear", "PAID@EXAMPLE.TEST")
        run("clear", "paid@example.test")
        assert json.loads(run("list", "--json").stdout) == []
        for malformed in (
            "{",
            "[]",
            '{"version":1,"subscriptions":[]}',
            '{"version":true,"subscriptions":{}}',
            '{"version":1,"subscriptions":{"paid@example.test":{"plan":"Pro","renewsOn":"bad","payment":"card","amountUsd":null,"note":null}}}',
        ):
            registry.write_text(malformed)
            assert json.loads(run("list", "--json").stdout) == []
    after = (real.read_bytes(), real.stat().st_mtime_ns) if real.exists() else None
    assert before == after, "real subscription registry changed"
    print("subscriptions: set/list/upsert/clear and invalid/malformed input passed")
    print("subscriptions: UTC health boundaries passed without a DB; real registry unchanged")


if __name__ == "__main__":
    main()
