from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.modules.pools import gemini_meter, gemini_vendor
from app.modules.pools.gemini_quota import READING_SCHEMA, gemini_usage_path, load_reading
from app.modules.pools.grok_weekly import write_reading
from app.usage_export import push

HOSTS = ("https://daily-cloudcode-pa.googleapis.com", "https://cloudcode-pa.googleapis.com")
MODELS_PATH = "/v1internal:fetchAvailableModels"
SUMMARY_PATH = "/v1internal:retrieveUserQuotaSummary"
USER_AGENT = "antigravity/hub/1.0.0 (aidev_client; os_type=linux; arch=x64; cl=963137146)"
TOKEN_FILE = "antigravity-oauth-token"
HEADLINE_CADENCE = timedelta(minutes=30)
OTHER_CADENCE = timedelta(hours=1)
REQUEST_TIMEOUT = 20
READS = ("models", "summary")


class Refused(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _access(token_path: Path) -> tuple[str | None, datetime | None]:
    document = json.loads(token_path.read_text(encoding="utf-8"))
    token = document.get("token") if isinstance(document, dict) else None
    if not isinstance(token, dict):
        raise ValueError("token")
    access = token.get("access_token")
    return (access if isinstance(access, str) and access else None), gemini_vendor.parse_time(token.get("expiry"))


def _post(access: str, path: str) -> Any:
    last: Exception = OSError("no host")
    for host in HOSTS:
        request = urllib.request.Request(
            host + path,
            data=b"{}",
            method="POST",
            headers={
                "Authorization": f"Bearer {access}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 429):
                raise Refused(exc.code) from None
            last = exc
        except (OSError, json.JSONDecodeError) as exc:
            last = exc
    raise last


def _due(name: str, reads: dict[str, Any], meter: dict[str, Any], now: datetime) -> bool:
    found = reads.get(name)
    read: dict[str, Any] = found if isinstance(found, dict) else {}
    at = gemini_vendor.parse_time(read.get("at"))
    if at is None:
        return True
    if read.get("headline"):
        last_request = gemini_vendor.parse_time(meter.get("lastRequestAt"))
        return now - at >= HEADLINE_CADENCE or (last_request is not None and last_request >= at)
    return now - at >= OTHER_CADENCE


def _error(kind: str, status: int | None, now: datetime) -> dict[str, Any]:
    return {"kind": kind, "status": status, "at": _stamp(now)}


def _vendor(
    agy_dir: Path, previous: dict[str, Any], meter: dict[str, Any], now: datetime, enabled: bool
) -> dict[str, Any]:
    token_path = agy_dir / TOKEN_FILE
    kept = {k: previous.get(k) for k in ("reads", "observedAt", "tokenExpiresAt") if previous.get(k) is not None}
    if not enabled:
        login = "off" if token_path.is_file() else "signed_out"
        return {"source": "none", "login": login, "reported": False, "windows": [], "buckets": [], "lastError": None}
    vendor: dict[str, Any] = {"source": previous.get("source", "none"), **kept, "lastError": None}
    reads: dict[str, Any] = dict(vendor.get("reads") or {})
    try:
        access, expires = _access(token_path)
    except (OSError, ValueError, json.JSONDecodeError):
        vendor.update(login="signed_out", lastError=_error("signed_out", None, now))
        return _finish(vendor, reads)
    vendor["tokenExpiresAt"] = gemini_vendor.iso(expires)
    if access is None or expires is None or expires <= now:
        vendor.update(login="idle")
        return _finish(vendor, reads)
    vendor["login"] = "ok"
    parsers = {
        "models": (MODELS_PATH, gemini_vendor.model_windows),
        "summary": (SUMMARY_PATH, gemini_vendor.summary_windows),
    }
    for name in READS:
        if not _due(name, reads, meter, now):
            continue
        path, parse = parsers[name]
        try:
            headline, buckets = parse(_post(access, path), now)
        except Refused as exc:
            vendor.update(
                login="refused" if exc.status in (401, 403) else "ok", lastError=_error("http", exc.status, now)
            )
            break
        except ValueError:
            vendor["lastError"] = _error("parse", None, now)
            continue
        except OSError as exc:
            vendor["lastError"] = _error("network", getattr(exc, "code", None), now)
            continue
        reads[name] = {"at": _stamp(now), "headline": headline, "buckets": buckets}
        vendor["observedAt"] = _stamp(now)
    return _finish(vendor, reads)


def _finish(vendor: dict[str, Any], reads: dict[str, Any]) -> dict[str, Any]:
    vendor["reads"] = reads
    sources = {"models": "fetch_available_models", "summary": "quota_summary"}
    chosen = next((name for name in READS if (reads.get(name) or {}).get("headline")), None)
    vendor["windows"] = reads[chosen]["headline"] if chosen else []
    vendor["reported"] = chosen is not None
    if chosen:
        vendor["source"] = sources[chosen]
    elif reads:
        vendor["source"] = sources[next(name for name in READS if name in reads)]
    buckets: dict[str, Any] = {}
    for name in READS:
        for bucket in (reads.get(name) or {}).get("buckets") or []:
            buckets.setdefault(bucket["id"], bucket)
    vendor["buckets"] = list(buckets.values())
    return vendor


def _meter(agy_dir: Path, meter_path: Path, now: datetime) -> dict[str, Any]:
    try:
        state = json.loads(meter_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = None
    try:
        state, _ = gemini_meter.scan(agy_dir, state, now)
        write_reading(meter_path, state)
    except (OSError, ValueError) as exc:
        return {"source": "agy_store", "scannedAt": _stamp(now), "ok": False, "error": type(exc).__name__}
    return gemini_meter.view(state, now, gemini_vendor.is_headline)


def collect(out: Path, *, pushing: bool, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    agy_dir = Path(os.environ.get("AGENT_LB_GEMINI_AGY_DIR", "~/.gemini/antigravity-cli")).expanduser()
    meter_path = Path(os.environ.get("AGENT_LB_GEMINI_METER", "~/.agent-lb/seats/gemini-meter.json")).expanduser()
    enabled = os.environ.get("AGENT_LB_GEMINI_VENDOR", "off") == "on"
    previous = load_reading(out) or {}
    meter = _meter(agy_dir, meter_path, current)
    old_vendor = previous.get("vendor") if isinstance(previous.get("vendor"), dict) else {}
    reading = {
        "schema": READING_SCHEMA,
        "host": socket.gethostname().split(".")[0],
        "plan": os.environ.get("AGENT_LB_GEMINI_PLAN", "Google AI Ultra 20x"),
        "checkedAt": _stamp(current),
        "vendor": _vendor(agy_dir, old_vendor, meter, current, enabled),
        "meter": meter,
    }
    write_reading(out, reading)
    destination = os.environ.get("AGENT_LB_GEMINI_QUOTA_PUSH", "ax42:.agent-lb/seats")
    if pushing and destination:
        try:
            push([out], destination)
        except (OSError, RuntimeError) as exc:
            print(f"push failed: {type(exc).__name__}")
    return reading


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Meter agy's Gemini use and read Google's quota into gemini.json.")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args(argv)
    reading = collect(args.out or gemini_usage_path(), pushing=not args.no_push)
    vendor, meter = reading["vendor"], reading["meter"]
    print(
        json.dumps(
            {
                "login": vendor.get("login"),
                "reported": vendor.get("reported"),
                "meterOk": meter.get("ok"),
                "requests5h": (meter.get("windows") or {}).get("5h", {}).get("requests"),
            }
        )
    )


if __name__ == "__main__":
    main()
