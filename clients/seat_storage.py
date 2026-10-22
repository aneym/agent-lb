from __future__ import annotations

import fcntl
import fnmatch
import json
import os
import re
import signal
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, NoReturn, TypeVar
from zoneinfo import ZoneInfo

VENDORS = ("cursor", "devin", "muse")


MUSE_PIN = "Muse Code 1.4.9 (1.4.9-R6297.1)"


MUSE_MIN = (1, 4, 9)


def muse_version_ok(text: str) -> bool:
  found = re.search(r"Muse Code (\d+)\.(\d+)\.(\d+)", text)
  version = tuple(map(int, found.groups())) if found else ()
  return MUSE_MIN <= version < (MUSE_MIN[0] + 1,)


SEAT_FOR_VENDOR = {"cursor": "cursor-seat", "devin": "devin-seat", "muse": "muse-seat-local"}


EXIT_OK = 0


EXIT_FAILED = 1


EXIT_NO_ACCOUNT = 2


EXIT_CAPACITY_WAIT = 4


ROUTE_EXIT_WAIT = 75


STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


STOP_GRACE_SECONDS = 10.0


KILL_DRAIN_SECONDS = 3.0


RELEASE_BUDGET_SECONDS = 8.0


STOP_POLL_SECONDS = 0.2


MUSE_STALL_SECONDS = 600.0


PROBE_TIMEOUT_SECONDS = 20.0


DEFAULT_RUN_TIMEOUT_SECONDS = 3600.0


DEFAULT_COOLDOWN = timedelta(hours=1)


MODEL_UNAVAILABLE_TTL = timedelta(hours=1)


RUNS_KEPT = 200


LEASES_KEPT = timedelta(days=7)


ERROR_CHARS = 400


FOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")


T = TypeVar("T")


DEVIN_FREE_MODEL = re.compile(r"^swe-\d+-(?:high|medium|max)$")


LIMIT_RE = re.compile(
    r"rate.?limit|usage limit|quota|too many requests|\b429\b|limit (?:reached|exceeded)|"
    r"out of (?:credits|usage|requests)|exceeded your|resource.?exhausted|upgrade your plan",
    re.I,
)


AUTH_RE = re.compile(
    r"not logged in|authentication required|unauthori[sz]ed|\b401\b|invalid api key|api key is invalid|"
    r"log ?in again|token (?:has )?expired|reauth",
    re.I,
)


MODEL_UNAVAILABLE_RE = re.compile(r"Cannot use this model:\s*\S+", re.I)


TRANSIENT_RE = re.compile(r"Failed to reach the Cursor API|connect ETIMEDOUT", re.I)


PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


RETRY_AFTER_RE = re.compile(r"try again in (\d+)\s*(second|minute|hour)", re.I)


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


def usage_since(record: dict[str, Any], since: datetime) -> dict[str, Any]:
  runs = [run for run in record.get("runs") or [] if (parse_ts(run.get("ts")) or since) > since]
  return {
    "runs": len(runs),
    "ok": sum(1 for run in runs if run.get("ok")),
    "wall_s": round(sum(float(run.get("wall_s") or 0) for run in runs), 1),
    "tokens_in": sum(int(run.get("tokens_in") or 0) for run in runs),
    "tokens_out": sum(int(run.get("tokens_out") or 0) for run in runs),
  }


def cursor_pool(model: str) -> str:
  managed = Path.home() / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
  repo = Path(__file__).resolve().parent.parent / "config" / "coding-agents" / "routing-table.json"
  path = Path(os.environ.get("ROUTE_TABLE") or (managed if managed.is_file() else repo)).expanduser()
  try:
    table = json.loads(path.read_text(encoding="utf-8"))
    definitions = table["cli_pools"]["cursor"]["pools"]
    return next(
      pool["id"]
      for pool in definitions
      if isinstance(pool, dict)
      and isinstance(pool.get("id"), str)
      and isinstance(pool.get("models"), list)
      and any(fnmatch.fnmatchcase(model, pattern) for pattern in pool["models"] if isinstance(pattern, str))
    )
  except (OSError, ValueError, KeyError, TypeError, StopIteration):
    return "cursor-other"


def cooling(record: dict[str, Any], pool: str | None = None) -> datetime | None:
  values = [record.get("cooldown_until")]
  if pool:
    values.append((record.get("cooldowns") or {}).get(pool))
  return next((until for value in values if (until := parse_ts(value)) and until > now()), None)


def mark_limit(record: dict[str, Any], vendor: str, model: str, text: str) -> None:
  if vendor != "cursor":
    record["cooldown_until"] = iso(cooldown_until(text))
    return
  pool = cursor_pool(model)
  match = re.search(r"reset when your monthly cycle ends on (\d{1,2})/(\d{1,2})/(\d{4})", text, re.I)
  try:
    month, day, year = map(int, match.groups())  # pyrefly: ignore[missing-attribute]
    until = datetime(year, month, day, tzinfo=timezone.utc)
  except (AttributeError, ValueError):
    until = None
  if until:
    record.setdefault("limits", {})[pool] = {"at": iso(now()), "until": iso(until), "source": "vendor"}
  record.setdefault("cooldowns", {})[pool] = iso(until or cooldown_until(text))


def active_leases(state: dict[str, Any], account_id: str, current: datetime) -> list[dict[str, Any]]:
  return [
    lease
    for lease in state.get("leases", {}).values()
    if lease.get("account") == account_id
    and lease.get("released_at") is None
    and (parse_ts(lease.get("expires_at")) or current) > current
  ]


def _count(value: Any) -> int:
  try:
    number = int(value or 0)
  except (TypeError, ValueError, OverflowError):
    return 0
  return number if number >= 0 else 0


def record_lease_usage(account: dict[str, Any], row: dict[str, Any]) -> None:
  line = {
    "ts": row.get("ts"),
    "model": row.get("model"),
    "ok": bool(row.get("ok")),
    "wall_s": row.get("wall_s"),
    "tokens_in": _count(row.get("tokens_in")),
    "tokens_out": _count(row.get("tokens_out")),
    "cache_read_tokens": _count(row.get("cache_read_tokens")),
  }
  try:
    lease_dir = Path(account["lease_dir"])
    log_path = lease_dir / "usage.jsonl"
    with log_path.open("a", encoding="utf-8") as stream:
      stream.write(json.dumps(line, sort_keys=True) + "\n")
    totals = {"tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0}
    last_model = row.get("model")
    for line in log_path.read_text(encoding="utf-8").splitlines():
      if not line.strip():
        continue
      try:
        item = json.loads(line)
      except json.JSONDecodeError:
        continue
      if not isinstance(item, dict):
        continue
      for field in totals:
        try:
          totals[field] += int(item.get(field) or 0)
        except (TypeError, ValueError, OverflowError):
          continue
      if item.get("model"):
        last_model = item["model"]
    write_json(lease_dir / "usage.json", {"model": last_model, **totals})
  except (OSError, UnicodeError, TypeError, ValueError):
    return


def record_run(record: dict[str, Any], run: dict[str, Any]) -> None:
  run = dict(run)
  for field in ("tokens_in", "tokens_out", "cache_read"):
    if run.get(field) is None:
      continue
    try:
      run[field] = int(run.get(field) or 0)
    except (TypeError, ValueError, OverflowError):
      run[field] = 0
  record["runs"] = ((record.get("runs") or []) + [run])[-RUNS_KEPT:]
  if run.get("model"):
    day = (parse_ts(run.get("ts")) or now()).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    bucket = (
      record.setdefault("daily", {})
      .setdefault(day, {})
      .setdefault(run["model"], {"runs": 0, "ok": 0, "tokens_in": 0, "tokens_out": 0, "cache_read": 0})
    )
    bucket["runs"] += 1
    if "ok" in bucket:
      bucket["ok"] += int(run.get("ok") is True)
    bucket["tokens_known"] = bucket.get(
      "tokens_known", bucket["runs"] == 1 or (bucket.get("tokens_in", 0) + bucket.get("tokens_out", 0)) > 0
    ) and all(run.get(field) is not None for field in ("tokens_in", "tokens_out"))
    for field in ("tokens_in", "tokens_out", "cache_read"):
      bucket[field] += int(run.get(field) or 0)


def release_usage(model: str | None, path: str | None) -> dict[str, Any]:
  empty = {"model": None, "tokens_in": None, "tokens_out": None, "cache_read": None}
  if not model or not path:
    return empty
  try:
    usage = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(usage, dict):
      return empty
    counts = {
      field: usage.get(source)
      for field, source in (
        ("tokens_in", "tokens_in"),
        ("tokens_out", "tokens_out"),
        ("cache_read", "cache_read_tokens"),
      )
    }
    counts = {
      field: int(value) if type(value) is float and value.is_integer() else value
      for field, value in counts.items()
    }
    if any(value is not None and (type(value) is not int or value < 0) for value in counts.values()):
      return empty
    return {"model": model, **counts}
  except (OSError, UnicodeError, json.JSONDecodeError):
    return empty


SEAT_CURSOR_MODELS: dict[str, tuple[str, ...]] = {}


def classify(text: str) -> str | None:
  if AUTH_RE.search(text):
    return "auth"
  if LIMIT_RE.search(text):
    return "limit"
  if MODEL_UNAVAILABLE_RE.search(text):
    return "model_unavailable"
  if TRANSIENT_RE.search(text):
    return "transient_infra"
  return None


def cooldown_until(text: str) -> datetime:
  match = RETRY_AFTER_RE.search(text)
  if match:
    unit = {"second": "seconds", "minute": "minutes", "hour": "hours"}[match.group(2).lower()]
    return now() + timedelta(**{unit: int(match.group(1))})
  return now() + DEFAULT_COOLDOWN


def order_accounts(
  registry: list[dict[str, Any]], state: dict[str, Any], vendor: str, model: str | None = None
) -> list[dict[str, Any]]:
  window = now() - timedelta(hours=5)
  ready = []
  for account in registry:
    if account.get("vendor") != vendor or account.get("enabled", True) is False:
      continue
    record = state["accounts"].get(account["id"], {})
    pool = cursor_pool(model) if vendor == "cursor" and model else None
    peers = [record]
    if pool and account.get("plan"):
      peers = [
        state["accounts"].get(peer["id"], {})
        for peer in registry
        if peer.get("vendor") == vendor and peer.get("plan") == account["plan"]
      ]
    pool_cooling = pool and any(
      (until := parse_ts((peer.get("cooldowns") or {}).get(pool))) and until > now() for peer in peers
    )
    unavailable = parse_ts((record.get("model_unavailable") or {}).get(model)) if model else None
    if record.get("auth_ok") is False or cooling(record) or pool_cooling or (unavailable and unavailable > now()):
      continue
    leases = [lease for lease in state.get("leases", {}).values() if lease.get("account") == account["id"]]
    recent = usage_since(record, window)["runs"] + len(active_leases(state, account["id"], now()))
    last_use = max([record.get("last_run_at") or ""] + [lease.get("created_at") or "" for lease in leases])
    ready.append((recent, last_use, account))
  ready.sort(key=lambda item: (item[0], item[1]))
  return [account for _, _, account in ready]


def append_ledger(rows: list[dict[str, Any]]) -> None:
  path = ledger_path()
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("a", encoding="utf-8") as stream:
    for row in rows:
      stream.write(json.dumps(row, sort_keys=True) + "\n")


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


EFFORT_SUFFIX = re.compile(r"-(?:low|medium|high|xhigh|max)$")
