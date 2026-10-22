from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, NoReturn
from zoneinfo import ZoneInfo

from clients.seat_constants import EXIT_FAILED, LEASES_KEPT, VENDORS, T


def seat_home() -> Path:
    return Path(os.environ.get("SEAT_HOME") or Path.home() / ".agent-lb" / "seats").expanduser()


def registry_path() -> Path:
    return seat_home() / "accounts.json"


def state_path() -> Path:
    return seat_home() / "state.json"


def ledger_path() -> Path:
    return Path(os.environ.get("ROUTE_LEDGER") or Path.home() / ".claude" / "logs" / "dispatch.jsonl").expanduser()


def cli_bin(vendor: str) -> str:
    lb = Path.home() / ".agent-lb/bin/muse-lb-launch"
    binaries = {"cursor": "cursor-agent", "devin": "devin", "muse": str(lb) if lb.is_file() else "muse"}
    return os.environ.get(f"SEAT_{vendor.upper()}_BIN") or binaries[vendor]


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def route_command(*args: str) -> list[str]:
    route = os.environ.get("ROUTE_BIN") or str(Path.home() / ".agent-lb" / "bin" / "route")
    try:
        with open(route, "rb") as handle:
            python = b"python" in handle.readline(200)
    except OSError:
        python = False
    return [sys.executable, route, *args] if python else [route, *args]


def fail(message: str, code: int = EXIT_FAILED) -> NoReturn:
    print(f"seat: {message}", file=sys.stderr)
    raise SystemExit(code)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path}: {error}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def load_registry() -> list[dict[str, Any]]:
    document = read_json(registry_path(), {"version": 1, "accounts": []})
    accounts = document.get("accounts") if isinstance(document, dict) else None
    return [account for account in accounts or [] if isinstance(account, dict) and account.get("id")]


def lease_accounts(vendor: str | None = None) -> list[dict[str, Any]]:
    root = seat_home() / "leases"
    if not root.is_dir():
        return []
    registered = {account["id"] for account in load_registry()}
    horizon = now() + timedelta(seconds=60)
    newest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    for directory in children:
        if not directory.is_dir():
            continue
        try:
            meta = json.loads((directory / "lease.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict) or meta.get("lease") != directory.name:
            continue
        meta_vendor = meta.get("vendor")
        if meta_vendor not in VENDORS or (vendor is not None and meta_vendor != vendor):
            continue
        expires = parse_ts(meta.get("expires_at"))
        if expires is None or expires <= horizon:
            continue
        account_id = meta.get("account")
        if not isinstance(account_id, str) or not account_id or account_id in registered:
            continue
        record = {
            "id": account_id,
            "vendor": meta_vendor,
            "auth": "lease",
            "lease": meta["lease"],
            "lease_dir": str(directory),
            "expires_at": meta["expires_at"],
            "enabled": True,
        }
        previous = newest.get(account_id)
        if previous is None or expires >= previous[0]:
            newest[account_id] = (expires, record)
    return [record for _, record in newest.values()]


def load_state() -> dict[str, Any]:
    state = read_json(state_path(), {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", 1)
    if not isinstance(state.get("accounts"), dict):
        state["accounts"] = {}
    if not isinstance(state.get("leases"), dict):
        state["leases"] = {}
    if not state.get("daily_ok_migrated"):
        for record in state["accounts"].values():
            kept: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for run in record.get("runs") or []:
                stamp = parse_ts(run.get("ts"))
                if stamp and run.get("model"):
                    day = stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat()
                    kept.setdefault((day, run["model"]), []).append(run)
            for day, models in (record.get("daily") or {}).items():
                for model, bucket in models.items():
                    runs = kept.get((day, model), [])
                    if (
                        "ok" not in bucket
                        and len(runs) == bucket.get("runs")
                        and all(isinstance(run.get("ok"), bool) for run in runs)
                    ):
                        bucket["ok"] = sum(run["ok"] for run in runs)
        state["daily_ok_migrated"] = True
    return state


@contextmanager
def state_lock() -> Iterator[None]:
    path = seat_home() / "state.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def update_state(apply: Callable[[list[dict[str, Any]], dict[str, Any]], T]) -> T:
    with state_lock():
        registry = load_registry()
        state = load_state()
        result = apply(registry, state)
        threshold = now() - LEASES_KEPT
        for lease_id, lease in list(state["leases"].items()):
            stamp = parse_ts(lease.get("released_at") or lease.get("expires_at"))
            if stamp is not None and stamp < threshold:
                del state["leases"][lease_id]
        save_state(state, registry)
        return result


def save_state(state: dict[str, Any], registry: list[dict[str, Any]]) -> None:
    known = {account["id"]: account for account in registry}
    accounts = state["accounts"]
    for account_id in list(accounts):
        if account_id not in known:
            del accounts[account_id]
    for account_id, account in known.items():
        record = accounts.setdefault(account_id, {})
        record["vendor"] = account.get("vendor")
        record["auth"] = account.get("auth")
        record["enabled"] = account.get("enabled", True) is not False
        record["tier_override"] = account.get("tier")
        record["cycle_day"] = account.get("cycle_day")
        record["plan"] = account.get("plan")
        record["email"] = account.get("email") or record.get("email")
        cutoff = (now() - timedelta(days=62)).date().isoformat()
        record["daily"] = {day: usage for day, usage in (record.get("daily") or {}).items() if day >= cutoff}
    state["updated_at"] = iso(now())
    write_json(state_path(), state)


def expand(value: Any) -> Path | None:
    return Path(value).expanduser() if isinstance(value, str) and value else None


def private_file(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(content)
    finally:
        if fd >= 0:
            os.close(fd)


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
