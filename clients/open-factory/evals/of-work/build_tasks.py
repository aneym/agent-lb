#!/usr/bin/env python3
"""Build Harbor task dirs for of-work from a frozen candidate file.

Each candidate is a merged commit whose tests fail at its parent and pass at the fix. The task
gives the agent the parent tree (via `of-checkout`) and the instruction. The verifier restores the
touched test files to the parent, applies the fix's test changes and runs the recorded
FAIL_TO_PASS and PASS_TO_PASS node ids. The oracle applies the rest of the fix.

Task dirs are written outside git: agent-rails is private and this repo is public.

    python3 build_tasks.py ~/.agent-lb/of/dataset/v0/dev.json ~/.agent-lb/of/dataset/v0/tasks
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path

REPOS = {
    "agent-lb": Path("/Volumes/StudioExt/repos/agent-lb"),
    "agent-rails": Path("/Volumes/StudioExt/repos/agent-rails"),
}
BASE_IMAGES = {
    "agent-lb": "of-work/agent-lb-base:328b33b849ec",
    "agent-rails": "of-work/agent-rails-base:7adf4749e52f",
}
PYTEST = {
    "agent-lb": ".venv/bin/python -m pytest -p no:cacheprovider --timeout=180 -q",
    "agent-rails": (
        "env RAILS_TEST_DB=sqlite CONTENT_AGENT_ENV=development RAILS_MODAL_ENABLE=0 RAILS_HERMES_LIVE=0 "
        "RAILS_LIVE_LB=0 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 "
        ".venv/bin/python -m pytest -p no:cacheprovider -q"
    ),
}
# Agents reach agent-lb on the host and nothing else: agent-lb is public on GitHub, so open
# egress would let a trial fetch the fix.
ALLOWED_HOSTS = ["host.docker.internal"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith("tests/")
        or "/tests/" in path
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def node_ids(value: object) -> list[str]:
    return json.loads(value) if isinstance(value, str) else list(value or [])


TASK_TOML = """schema_version = "1.4"
artifacts = []

[task]
name = "of-work/{id}"
version = "1.0.0"
description = {description}
authors = []
keywords = [{keywords}]

[metadata]
repo = "{repo}"
class = "{cls}"
difficulty = {difficulty}
base_sha = "{base_sha}"
fix_sha = "{fix_sha}"

[verifier]
timeout_sec = 900.0
collect = []

[verifier.env]

[agent]
timeout_sec = 2400.0

[environment]
network_mode = "allowlist"
allowed_hosts = {allowed}
build_timeout_sec = 1200.0
os = "linux"
mcp_servers = []

[environment.env]

[solution.env]
"""

TEST_SH = """#!/bin/bash
# Reward 1 iff every required test (tests/required.json) passes on the agent's tree.
set -u
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
cd /app
# Put the touched test files back to the task's base, then apply the fix's test changes.
{restore}
git apply --whitespace=nowarn /tests/tests.patch || {{ echo "tests.patch did not apply"; exit 0; }}
timeout 840 {pytest} -rA {files} > /logs/verifier/pytest.log 2>&1
tail -30 /logs/verifier/pytest.log
python3 /tests/grade.py /logs/verifier/pytest.log /tests/required.json /logs/verifier
exit 0
"""

GRADE_PY = """import json, re, sys
from pathlib import Path

log, required, out = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3])
outcomes = {}
for line in log.read_text(errors="replace").splitlines():
    match = re.match(r"(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (\\S+)", line)
    if match and "::" in match.group(2):
        # A test can report twice (a teardown error after a pass); any non-pass wins.
        if outcomes.get(match.group(2), "PASSED") == "PASSED":
            outcomes[match.group(2)] = match.group(1)
(out / "outcomes.json").write_text(json.dumps(outcomes, indent=1, sort_keys=True))
need = required["fail_to_pass"] + required["pass_to_pass"]
missing = [n for n in need if outcomes.get(n) != "PASSED"]
print(f"required {len(need)}, not passed {len(missing)}", *missing[:20], sep="\\n  ")
(out / "reward.txt").write_text("1\\n" if need and not missing else "0\\n")
"""

SOLVE_SH = """#!/bin/bash
set -eu
cd /app
git apply --whitespace=nowarn /solution/solution.patch
"""


def build(candidate: dict, out: Path) -> dict:
    repo = REPOS[candidate["repo"]]
    base, fix = candidate["base_sha"], candidate["fix_sha"]
    changed = [line for line in git(repo, "diff", "--name-only", base, fix).splitlines() if line]
    tests = [path for path in changed if is_test_path(path)]
    source = [path for path in changed if not is_test_path(path)]
    if not tests or not source:
        return {"id": candidate["id"], "skipped": f"tests={len(tests)} source={len(source)}"}
    in_base = set(git(repo, "ls-tree", "-r", "--name-only", base, "--", *tests).splitlines())
    restore = [f"git checkout main -- {shlex.quote(p)}" for p in tests if p in in_base]
    restore += [f"rm -f {shlex.quote(p)}" for p in tests if p not in in_base]
    required = {
        "fail_to_pass": node_ids(candidate["fail_to_pass"]),
        "pass_to_pass": node_ids(candidate.get("pass_to_pass")),
    }
    files = sorted({node.split("::", 1)[0] for node in required["fail_to_pass"] + required["pass_to_pass"]})

    task = out / candidate["id"]
    for sub in ("environment", "tests", "solution"):
        (task / sub).mkdir(parents=True, exist_ok=True)
    (task / "instruction.md").write_text(
        candidate["instruction"].strip() + "\n\nThe repository is checked out at /app.\n"
    )
    (task / "task.toml").write_text(
        TASK_TOML.format(
            id=candidate["id"],
            description=json.dumps(candidate["instruction"].strip().splitlines()[0][:200]),
            keywords=", ".join(json.dumps(k) for k in (candidate["repo"], candidate["class"])),
            repo=candidate["repo"],
            cls=candidate["class"],
            difficulty=int(candidate.get("difficulty") or 0),
            base_sha=base,
            fix_sha=fix,
            allowed=json.dumps(ALLOWED_HOSTS),
        )
    )
    dockerfile = f"FROM {BASE_IMAGES[candidate['repo']]}\n"
    # Tests and agents shell out to `python3`; the image has no system python.
    dockerfile += f"RUN of-checkout {base}\nWORKDIR /app\nENV PATH=/app/.venv/bin:$PATH\n"
    (task / "environment" / "Dockerfile").write_text(dockerfile)
    (task / "tests" / "tests.patch").write_text(git(repo, "diff", "--binary", base, fix, "--", *tests))
    (task / "solution" / "solution.patch").write_text(git(repo, "diff", "--binary", base, fix, "--", *source))
    test_sh = TEST_SH.format(
        restore="\n".join(restore), pytest=PYTEST[candidate["repo"]], files=" ".join(shlex.quote(f) for f in files)
    )
    (task / "tests" / "grade.py").write_text(GRADE_PY)
    (task / "tests" / "required.json").write_text(json.dumps(required, indent=1) + "\n")
    for path, body in ((task / "tests" / "test.sh", test_sh), (task / "solution" / "solve.sh", SOLVE_SH)):
        path.write_text(body)
        path.chmod(0o755)
    return {
        "id": candidate["id"],
        "tests": len(tests),
        "source": len(source),
        "required": len(required["fail_to_pass"]) + len(required["pass_to_pass"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("candidates", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    data = json.loads(args.candidates.read_text())
    candidates = data if isinstance(data, list) else data["candidates"]
    args.out.mkdir(parents=True, exist_ok=True)
    results = [build(c, args.out) for c in candidates]
    (args.out / "build.json").write_text(json.dumps(results, indent=2) + "\n")
    skipped = [r for r in results if "skipped" in r]
    print(f"built {len(results) - len(skipped)} of {len(results)} tasks in {args.out}")
    for r in skipped:
        print(f"  skipped {r['id']}: {r['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
