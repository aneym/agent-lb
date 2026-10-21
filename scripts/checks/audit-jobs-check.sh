#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
t=$(mktemp -d "${TMPDIR:-/tmp}/audit-jobs-check.XXXXXX")
trap 'rm -rf "$t"' EXIT HUP INT TERM
job=$(python3 -I -c '
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
root = Path.home() / ".agent-rails/jobs/seat-run"
today = root / "archive" / datetime.now(UTC).strftime("%Y-%m-%d")
paths = list(root.glob("*.rc")) + list(today.glob("*.rc")) + list(today.glob("dup-*/*.rc"))
for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
    name = path.stem
    if not name.startswith(("opus-seat-", "verifier-")):
        continue
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@._:/-]{0,63}", name):
        continue
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        continue
    if isinstance(record, dict) and record.get("job_id") == name:
        probe = subprocess.run(
            [".venv/bin/python", "-m", "app.cli", "audit", "jobs", "--json", "--since", "24h", "--job", name],
            capture_output=True, text=True, timeout=100, check=True,
        )
        if json.loads(probe.stdout)["jobs"]:
            print(name)
            break
else:
    raise SystemExit("audit-jobs-check: no matching recorded job")
')
.venv/bin/python -m app.cli audit jobs --json --since 24h --job "$job" --job no-such-job-run-trace > "$t/result.json"
python3 -I -c '
import json
import sys
result = json.load(open(sys.argv[1]))
assert result["schema"] == 1
assert len(result["jobs"]) == 1
job = result["jobs"][0]
assert job["job_id"] == sys.argv[2]
assert job["requests"] >= 1
assert job["requests"] == len(job["calls"]) + job["calls_omitted"]
assert job["calls"]
assert all(call["end_at"].endswith("Z") and isinstance(call["latency_ms"], int) for call in job["calls"])
forbidden = {"request_id", "account_id", "session_id", "prompt", "headers", "error_message"}
def inspect(value):
    if isinstance(value, dict):
        assert not forbidden.intersection(value)
        for child in value.values():
            inspect(child)
    elif isinstance(value, list):
        for child in value:
            inspect(child)
inspect(result)
print("audit-jobs-check: ok {} requests={} cost_usd={}".format(job["job_id"], job["requests"], job["cost_usd"]))
' "$t/result.json" "$job" > "$t/summary"
rc=0
.venv/bin/python -m app.cli audit jobs --json --since 24h --job 'bad id' > "$t/bad.out" 2> "$t/bad.err" || rc=$?
test "$rc" -eq 2
cat "$t/summary"
