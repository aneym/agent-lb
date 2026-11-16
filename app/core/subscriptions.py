from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Subscription:
    plan: str
    renews_on: str
    payment: str
    amount_usd: float | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, str) or not self.plan.strip():
            raise ValueError("plan is required")
        if not isinstance(self.renews_on, str) or date.fromisoformat(self.renews_on).isoformat() != self.renews_on:
            raise ValueError("renews-on must be YYYY-MM-DD")
        if self.payment not in ("card", "single-use", "none"):
            raise ValueError("payment must be card, single-use or none")
        if self.amount_usd is not None and (
            type(self.amount_usd) not in (int, float) or not math.isfinite(self.amount_usd) or self.amount_usd < 0
        ):
            raise ValueError("amount must be a finite nonnegative number")
        if self.note is not None and not isinstance(self.note, str):
            raise ValueError("note must be text")

    def as_row(self) -> dict:
        return {
            "plan": self.plan,
            "renewsOn": self.renews_on,
            "payment": self.payment,
            "amountUsd": self.amount_usd,
            "note": self.note,
        }


_REMOTE_READ = (
    "import pathlib,sys; p=pathlib.Path.home()/sys.argv[1]; sys.stdout.write(p.read_text() if p.exists() else '')"
)

_REMOTE_WRITE = """import os,pathlib,sys,tempfile
p=pathlib.Path.home()/sys.argv[1]
fd,t=tempfile.mkstemp(dir=p.parent,prefix='.subscriptions-')
try:
    with os.fdopen(fd,'w') as f:
        os.fchmod(f.fileno(),0o600); f.write(sys.stdin.read()); f.flush(); os.fsync(f.fileno())
    os.replace(t,p)
finally:
    pathlib.Path(t).unlink(missing_ok=True)"""


class LedgerUnavailable(Exception):
    pass


def _state_dir() -> Path:
    return Path(os.getenv("AGENT_LB_STATE_DIR", str(Path.home() / ".agent-lb/state")))


def registry_path() -> Path:
    return _state_dir() / "subscriptions.json"


def ledger_home() -> tuple[str, str] | None:
    path = _state_dir() / "ledger.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        host, remote = data["ssh"], data["path"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LedgerUnavailable(f"{path} is unreadable: {type(exc).__name__}") from None
    if not isinstance(host, str) or not host or not isinstance(remote, str) or not remote:
        raise LedgerUnavailable(f"{path} needs ssh and path")
    return host, remote


def _ssh(home: tuple[str, str], script: str, stdin: str | None = None) -> str:
    host, remote = home
    command = shlex.join(["python3", "-c", script, remote])
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LedgerUnavailable(f"subscription ledger on {host}: {type(exc).__name__}") from None
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or [f"exit {result.returncode}"])[-1][:200]
        raise LedgerUnavailable(f"subscription ledger on {host}: {detail}")
    return result.stdout


def _read_ledger() -> str:
    home = ledger_home()
    return _ssh(home, _REMOTE_READ) if home else registry_path().read_text()


def load_subscriptions() -> dict[str, Subscription]:
    try:
        data = json.loads(_read_ledger())
        if type(data["version"]) is not int or data["version"] != 1 or not isinstance(data["subscriptions"], dict):
            return {}
        subscriptions = {}
        for email, row in data["subscriptions"].items():
            if not email or email != email.strip().lower():
                return {}
            subscriptions[email] = Subscription(
                row["plan"], row["renewsOn"], row["payment"], row["amountUsd"], row["note"]
            )
        return subscriptions
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        return {}


def save_subscriptions(subscriptions: dict[str, Subscription]) -> None:
    data = {
        "version": 1,
        "subscriptions": {email.strip().lower(): sub.as_row() for email, sub in subscriptions.items()},
    }
    home = ledger_home()
    if home:
        _ssh(home, _REMOTE_WRITE, json.dumps(data, indent=2, allow_nan=False))
        return
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".subscriptions-")
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def subscription_for(email: str) -> Subscription | None:
    return load_subscriptions().get(email.strip().lower())
