from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

LIVE_PORTS = {1455, 2455, 2457, 2459}
PLACEHOLDER = "sandbox-placeholder-not-a-credential"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def free_port(exclude: set[int]) -> int:
    for port in range(2470, 2600):
        if port in exclude or port in LIVE_PORTS:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no sandbox port free in 2470-2599")


def guard_port(port: int) -> None:
    if port < 2470 or port in LIVE_PORTS:
        raise RuntimeError(f"refusing port {port}: live or below the sandbox range")


def listener_pids(port: int) -> list[int]:
    proc = subprocess.run(
        [shutil.which("lsof") or "/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return sorted({int(line) for line in proc.stdout.split() if line.strip().isdigit()})


def listener_addresses(pid: int) -> list[str]:
    proc = subprocess.run(
        [shutil.which("lsof") or "/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return [line[1:] for line in proc.stdout.splitlines() if line.startswith("n")]


class InfraError(RuntimeError):
    pass


class Check:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.steps: list[dict] = []
        self.unverified: list[str] = []
        self.started_at = now_iso()

    def step(self, name: str, ok: bool, detail: str, evidence: list[str]) -> bool:
        self.steps.append({"name": name, "result": "pass" if ok else "fail", "detail": detail, "evidence": evidence})
        return ok

    def write(self, infra_error: str | None = None) -> int:
        if infra_error is not None:
            result = "infra_error"
        elif self.steps and all(s["result"] == "pass" and s["evidence"] for s in self.steps):
            result = "pass"
        else:
            result = "fail"
        failed = [s["name"] for s in self.steps if s["result"] != "pass"]
        if infra_error is not None:
            summary = f"Sandbox could not run: {infra_error}"
        elif result == "pass":
            summary = (
                "On a sandbox agent-lb the capped Claude caller tripped at the price-table request number, "
                "the unpriced model was refused before upstream, and uncapped traffic passed unchanged."
            )
        else:
            summary = f"Failed steps: {', '.join(failed) or 'no steps ran'}."
        doc = {
            "schema": 1,
            "lane": "lb-spend-cap",
            "component_ids": ["claude-lb"],
            "run_id": os.environ.get("HARDEN_RUN_ID") or self.out.name,
            "host": socket.gethostname(),
            "started_at": self.started_at,
            "ended_at": now_iso(),
            "result": result,
            "summary": summary,
            "steps": self.steps,
            "unverified": self.unverified,
        }
        (self.out / "result.json").write_text(json.dumps(doc, indent=2) + "\n")
        print(json.dumps(doc, indent=2))
        return {"pass": 0, "fail": 1}.get(result, 2)


def export_app(repo: Path, ref: str, dest: Path) -> str:
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=False, timeout=120, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref], check=True, timeout=30, capture_output=True, text=True
    ).stdout.strip()
    dest.mkdir(parents=True)
    archive = subprocess.Popen(["git", "-C", str(repo), "archive", sha], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout, check=True, timeout=300)
    if archive.wait(timeout=300) != 0:
        raise RuntimeError("git archive failed")
    return sha


def copy_tree(src: Path, dest: Path) -> str:

    listed = subprocess.run(
        ["git", "-C", str(src), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        timeout=120,
        capture_output=True,
    ).stdout.split(b"\0")
    dest.mkdir(parents=True)
    for raw in listed:
        if not raw:
            continue
        rel = Path(raw.decode())
        source = src / rel
        if not source.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    head = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "HEAD"], check=True, timeout=30, capture_output=True, text=True
    ).stdout.strip()
    return f"{head}+working-tree"


def scrub_dotenv(app_root: Path) -> None:

    for name in (".env", ".env.local"):
        target = app_root / name
        if target.exists() or target.is_symlink():
            target.unlink()
        if target.exists():
            raise InfraError(f"could not remove {target}")


def sandbox_env(app_root: Path, data: Path, upstream_port: int) -> dict[str, str]:
    env = {
        "HOME": str(data / "home"),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(app_root),
        "AGENT_LB_DATA_DIR": str(data),
        "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{data / 'store.db'}",
        "AGENT_LB_ENCRYPTION_KEY_FILE": str(data / "encryption.key"),
        "AGENT_LB_UPSTREAM_BASE_URL": f"http://127.0.0.1:{upstream_port}/backend-api",
        "AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL": f"http://127.0.0.1:{upstream_port}",
        "AGENT_LB_DASHBOARD_BOOTSTRAP_TOKEN": PLACEHOLDER,
        "AGENT_LB_DASHBOARD_AUTH_MODE": "disabled",
        "AGENT_LB_UPSTREAM_REQUEST_GZIP_ENABLED": "false",
        "AGENT_LB_FEDERATION_FORWARD_REQUEST_LOGS": "false",
        "AGENT_LB_FEDERATION_PEER_URL": "",
        "AGENT_LB_FEDERATION_TOKEN": "",
        "AGENT_LB_FEDERATION_MIRROR_TOKEN": "",
        "AGENT_LB_FEDERATION_TRANSFER_OUTBOUND_TOKEN": "",
        "AGENT_LB_FEDERATION_PUSH_PATH": str(data / "federation-push.json"),
        "AGENT_LB_FEDERATION_PUSH_SOURCES_PATH": str(data / "federation-push-sources.json"),
        "AGENT_LB_LOCAL_INSTANCE_ID": "lb-spend-cap-sandbox",
        "AGENT_LB_SEAT_STATE": str(data / "seat-state.json"),
        "ROUTE_TABLE": str(data / "routing-table.json"),
        "OF_DECIDER_JSON": str(data / "decider.json"),
    }
    for setting in (
        "METRICS",
        "USAGE_REFRESH",
        "MODEL_REGISTRY",
        "ACCOUNT_PULSE",
        "RESET_CREDIT_AUTO_REDEEM",
        "RESET_CREDIT_EXPIRY_REDEEM",
        "ACCOUNTS_CACHE_WARMER",
        "STICKY_SESSION_CLEANUP",
        "QUOTA_PLANNER_SCHEDULER",
        "HTTP_RESPONSES_SESSION_BRIDGE",
        "AUTH_GUARDIAN",
    ):
        env[f"AGENT_LB_{setting}_ENABLED"] = "false"
    return env


SEED_ACCOUNT = f"""
import asyncio
from datetime import timedelta
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal

async def main():
    enc = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(Account(
            id="sandbox-anthropic", provider="anthropic", chatgpt_account_id="sandbox-anthropic",
            email="sandbox@example.invalid", plan_type="max",
            access_token_encrypted=enc.encrypt("{PLACEHOLDER}"),
            refresh_token_encrypted=enc.encrypt("{PLACEHOLDER}"), id_token_encrypted=None,
            last_refresh=utcnow() + timedelta(days=1), status=AccountStatus.ACTIVE, deactivation_reason=None,
        ))
        session.add(Account(
            id="sandbox-openai", provider="openai", chatgpt_account_id="sandbox-openai",
            email="sandbox@example.invalid", plan_type="plus",
            access_token_encrypted=enc.encrypt("{PLACEHOLDER}"),
            refresh_token_encrypted=enc.encrypt("{PLACEHOLDER}"), id_token_encrypted=None,
            last_refresh=utcnow() + timedelta(days=1), status=AccountStatus.ACTIVE, deactivation_reason=None,
        ))
        await session.commit()

asyncio.run(main())
"""
