from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALIASES = {
    "implementation": "implement",
    "fix": "implement",
    "fix-forward": "implement",
    "migration": "implement",
    "sweep": "mechanical",
    "probe": "mechanical",
    "test-run": "mechanical",
    "investigation": "explore",
    "exploration": "explore",
}


def main() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        work = Path(directory)
        source = ROOT / "config/coding-agents/routing-table"
        table_path = work / "table"
        shutil.copytree(source, table_path)
        table = json.loads((source / "explore.json").read_text())
        aliases = json.loads((source / "models.json").read_text())["aliases"]
        (work / "api_models.json").write_text(json.dumps({"data": [{"id": "gpt-6.1-sol"}]}))
        env = dict(
            os.environ,
            HOME=directory,
            ROUTE_TABLE=str(table_path),
            ROUTE_FIXTURE_DIR=directory,
            ROUTE_DECISIONS_LOG=str(work / "decisions.jsonl"),
            ROUTE_LEDGER=str(work / "ledger.jsonl"),
        )
        calls = 0

        def dispatch(
            name: str, codex: int = 5, grok: int = 0, *flags: str, codex_pool: dict | None = None
        ) -> tuple[int, dict]:
            nonlocal calls
            calls += 1
            (table_path / "explore.json").write_text(json.dumps(table))
            pools = {
                "pools": [
                    {
                        "id": pool,
                        "headroomPercent": 80 if count else 0,
                        "eligibleAccounts": count,
                        "status": "ok" if count else "exhausted",
                    }
                    for pool, count in (("openai-codex", codex), ("grok", grok), ("anthropic-general", 5))
                ]
            }
            if codex_pool is not None:
                pools["pools"][0] = {"id": "openai-codex", **codex_pool}
            pool_path = work / "pools.json"
            pool_path.write_text(json.dumps(pools))
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "clients/route"),
                    "dispatch",
                    name,
                    "--dry-run",
                    "--pools-file",
                    str(pool_path),
                    "--json",
                    *flags,
                ],
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
            )
            assert result.returncode in (0, 2, 3), (result.stdout, result.stderr)
            return result.returncode, json.loads(result.stdout) if result.stdout else {}

        failures = []
        for pool in (
            {"status": "stale", "headroomPercent": 80, "eligibleAccounts": 5},
            {"status": "ok", "eligibleAccounts": 5},
        ):
            rc, row = dispatch("implement", codex_pool=pool)
            if not (rc == 0 and row["seat"] == "gpt-implementer" and row["model_alias"] == "sol-latest"):
                failures.append((pool, rc, row))
        assert not failures, failures
        for name, resolved in ALIASES.items():
            rc, row = dispatch(name)
            assert rc == 0 and row["model_alias"] == "sol-latest", (name, rc, row)
            assert row["class"] == resolved and row["requested_class"] == name, row
        for name in ("implement", "mechanical", "explore", "research", *ALIASES):
            rc, row = dispatch(name, 5, 5)
            assert rc == 0 and row["model_alias"] == "sol-latest", (name, rc, row)
        for name in ("implement", "mechanical", "explore", "research", *ALIASES):
            rc, row = dispatch(name, 0)
            assert rc == 3 and row["status"] == "wait", (name, rc, row)
        rc, row = dispatch("implement", 0, 5)
        assert (rc == 0 and row["seat"] == "grok-implementer") or rc == 3, row
        for tag in ("fix", "money-path", "contract", "trust-boundary", "ui-critical"):
            rc, row = dispatch("implement", 0, 5, "--tag", tag)
            assert rc == 3, (tag, rc, row)
        for name in ("explore", "research"):
            for arm in table["explore"]["arms"][name]:
                assert aliases[arm["model"]]["vendor"] != "anthropic", arm
        table["explore"]["enabled"] = True
        table["explore"]["max_share_per_class"] = 1
        table["explore"]["arms"]["implement"] = [
            {
                "id": "sol-experiment",
                "seat": "gpt-implementer",
                "model": "sol-latest",
                "effort": "low",
                "share": 1,
                "pool": "openai-codex",
            }
        ]
        rc, row = dispatch("implementation", 5, 0, "--seed", "1")
        assert rc == 0 and row["arm"] == "explore", row
        rc, row = dispatch("fix", 5, 0, "--seed", "1")
        assert rc == 0 and row["arm"] != "explore", row
        rc, row = dispatch("unknown-hands")
        assert rc == 2, row
        rc, row = dispatch("plan")
        assert rc == 0 and row["model_alias"] == "opus-latest", row
        print(f"PASS {calls} route dispatch CLI cases; hands exploration arms exclude Claude")


if __name__ == "__main__":
    main()
