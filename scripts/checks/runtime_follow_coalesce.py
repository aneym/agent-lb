from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    if done.returncode:
        raise AssertionError(f"{cmd[0]} rc={done.returncode}: {(done.stderr or done.stdout)[-400:]}")
    return done.stdout.strip()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def exercise(root: Path) -> None:
    origin, src, runtime, bins = (root / name for name in ("origin", "src", "runtime/agent-lb", "bin"))
    run(["git", "init", "--bare", "--initial-branch=main", str(origin)], cwd=root)
    run(["git", "clone", str(origin), str(src)], cwd=root)
    for key, value in (("user.name", "Runtime Check"), ("user.email", "runtime-check@example.invalid")):
        run(["git", "config", key, value], cwd=src)
    for rel in ("pyproject.toml", "uv.lock", "app/x.py", "docs/a.md", "scripts/s.py"):
        dest = src / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("initial\n")

    def push(rel: str, value: str) -> str:
        (src / rel).write_text(value + "\n")
        run(["git", "add", "."], cwd=src)
        run(["git", "commit", "-m", value], cwd=src)
        run(["git", "push", "origin", "main"], cwd=src)
        return run(["git", "rev-parse", "HEAD"], cwd=src)

    old_bin = '#!/bin/sh\nprintf "old\\n"\n'
    (src / "scripts/operator-tool").write_text(old_bin)
    push("docs/a.md", "baseline")
    archive = root / "tree.tar"
    run(["git", "archive", "--output", str(archive), "HEAD"], cwd=src)
    runtime.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(runtime, filter="tar")
    (runtime / ".venv/bin").mkdir(parents=True)
    (runtime / ".venv/bin/agent-lb").touch()
    (runtime / ".venv/.uv-lock-sha").write_text(hashlib.sha256((runtime / "uv.lock").read_bytes()).hexdigest() + "\n")
    bins.mkdir()
    (bins / "operator-tool").write_text(old_bin)
    restarts = root / "restarts.txt"
    stub = bins / "lb-restart"
    stub.write_text('#!/bin/sh\nprintf "restart\\n" >> "$RESTARTS"\n')
    stub.chmod(0o755)
    env = {
        **os.environ,
        "AGENT_LB_RUNTIME_DIR": str(runtime),
        "AGENT_LB_SYNC_SRC": str(src),
        "AGENT_LB_BIN_DIR": str(bins),
        "RESTARTS": str(restarts),
    }
    env.pop("AGENT_LB_FOLLOW_ALERT_TO", None)

    def cli(*args: str) -> str:
        return run([sys.executable, str(REPO / "scripts/runtime_follow.py"), *args], cwd=root, env=env)

    state_path = runtime.parent / "follow-state.json"

    def age(**seconds: int) -> None:
        state = json.loads(state_path.read_text())
        for key, offset in seconds.items():
            state[key] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - offset))
        state_path.write_text(json.dumps(state))

    def count() -> int:
        return len(restarts.read_text().splitlines()) if restarts.exists() else 0

    push("docs/a.md", "live")
    cli("tick")
    require((runtime / "docs/a.md").read_text() == "live\n", "docs did not sync live")
    require(not restarts.exists(), "docs triggered restart")
    require("= origin/main" in cli("status"), "live status is not in sync")
    want = push("app/x.py", "code one")
    cli("tick")
    require(count() == 0, "code restarted before quiet window")
    status = cli("status")
    require("restart pending" in status and "next eligible" in status, "missing pending status")
    cli("check")
    age(main_seen_at=900, restarted_at=3600)
    cli("tick")
    require(count() == 1, "eligible code did not restart exactly once")
    require(json.loads(state_path.read_text())["deployed"] == want, "deployed sha does not match main")
    push("app/x.py", "code two")
    cli("tick")
    age(main_seen_at=900)
    cli("tick")
    require(count() == 1 and "(gap)" in cli("status"), "minimum gap did not defer restart")
    cli("tick", "--force")
    require(count() == 2, "force did not restart")
    push("app/x.py", "code three")
    cli("tick")
    age(restart_pending_since=10800, main_seen_at=0)
    cli("tick")
    require(count() == 3, "ceiling did not restart")
    (src / "scripts/operator-tool").write_text('#!/bin/sh\nprintf "new\\n"\n')
    push("app/x.py", "rollback regression")
    cli("tick")
    require((bins / "operator-tool").read_text() == old_bin, "pending deploy replaced bin before backup")
    stub.write_text("#!/bin/sh\nexit 1\n")
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts/runtime_follow.py"), "tick", "--force"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    require(done.returncode == 1, "failed restart was not reported")
    require((bins / "operator-tool").read_text() == old_bin, "failed restart did not restore old bin")
    push("scripts/operator-tool", '#!/bin/sh\nprintf "live\\n"')
    push("app/x.py", "code three")
    cli("tick")
    require("live" in (bins / "operator-tool").read_text(), "no-restart bin update did not install")
    print("ok: live sync, pending status/check, quiet, gap, force, ceiling, bin rollback")


def plist_budget(root: Path) -> None:
    home = root / "home"
    plist = home / "Library/LaunchAgents/com.aneyman.agent-lb.plist"
    plist.parent.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "PYTHON_BIN": sys.executable}
    for prior, expected in ((330, 420), (900, 900)):
        plist.write_bytes(
            plistlib.dumps(
                {
                    "ExitTimeOut": prior,
                    "EnvironmentVariables": {
                        "UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN": "300",
                        "AGENT_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS": "30",
                    },
                }
            )
        )
        output = run(["bash", str(REPO / "scripts/install-service.sh"), "--print"], cwd=REPO, env=env)
        generated = plistlib.loads(output.encode())
        require(generated["ExitTimeOut"] == expected, f"wrong ExitTimeOut for {prior}: {generated['ExitTimeOut']}")
        require(plistlib.loads(plist.read_bytes())["ExitTimeOut"] == prior, "--print changed the installed plist")
    print("ok: plist generation repairs 330 to 420 and preserves operator timeout 900")


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="runtime-follow-check-") as temp:
            exercise(Path(temp))
            plist_budget(Path(temp))
    except (AssertionError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL runtime-follow: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
