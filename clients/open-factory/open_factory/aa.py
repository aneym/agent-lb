"""Artificial Analysis snapshots and non-routing model evidence."""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
MAP_PATH = Path(__file__).with_name("aa_map.json")
FETCH_INTERVAL_SECONDS = 24 * 60 * 60
EVIDENCE_MAX_AGE_SECONDS = 8 * 24 * 60 * 60


def snapshot_dir() -> Path:
    return Path(os.environ.get("OF_AA_DIR", "~/.agent-lb/of/aa")).expanduser()


def _age_seconds(snapshot: dict[str, Any], now: datetime) -> float | None:
    try:
        timestamp = datetime.fromisoformat(snapshot["fetched_at"].replace("Z", "+00:00"))
        return (now - timestamp).total_seconds()
    except (KeyError, AttributeError, TypeError, ValueError):
        return None


def _read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) and isinstance(data.get("data"), list) else None
    except (OSError, ValueError):
        return None


def _newest_snapshot(directory: Path) -> tuple[Path | None, dict[str, Any] | None]:
    for path in sorted(directory.glob("[0-9]*.json"), reverse=True):
        snapshot = _read_snapshot(path)
        if snapshot is not None:
            return path, snapshot
    return None, None


def fetch_models(key: str) -> dict[str, Any]:
    request = urllib.request.Request(API_URL, headers={"x-api-key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
        # An upstream echo of the credential must never reach disk or output.
        if key in payload:
            raise ValueError("Artificial Analysis response contains credential")
        data = json.loads(payload)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ValueError("Artificial Analysis returned an invalid models response")
        return data
    except (OSError, ValueError):
        # urllib exception strings may contain server-controlled text, including headers.
        raise ValueError("Artificial Analysis fetch failed or returned invalid JSON") from None


def _mapping() -> dict[str, list[dict[str, str]]]:
    return json.loads(MAP_PATH.read_text())


def summarize(snapshot: dict[str, Any]) -> dict[str, Any]:
    mapping = _mapping()
    lookup = {entry["slug"]: (alias, entry["variant"]) for alias, entries in mapping.items() for entry in entries}
    result: dict[str, Any] = {alias: [] for alias in mapping}
    unmapped: set[str] = set()
    for model in snapshot["data"]:
        if not isinstance(model, dict) or not isinstance(model.get("slug"), str):
            continue
        slug = model["slug"]
        match = lookup.get(slug)
        if match is None:
            unmapped.add(slug)
            continue
        alias, variant = match
        evaluations = model.get("evaluations") or {}
        pricing = model.get("pricing") or {}
        result[alias].append(
            {
                "slug": slug,
                "variant": variant,
                "intelligence_index": evaluations.get("artificial_analysis_intelligence_index"),
                "coding_index": evaluations.get("artificial_analysis_coding_index"),
                "price_blended_usd_per_1m": pricing.get("price_1m_blended_3_to_1"),
                "output_tokens_per_s": model.get("median_output_tokens_per_second"),
            }
        )
    result["unmapped"] = sorted(unmapped)
    return result


def sync(*, dry_run: bool = False, fixture: Path | None = None, force: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if dry_run:
        path = fixture or Path(__file__).resolve().parents[3] / "tests/fixtures/aa/models.json"
        snapshot = json.loads(path.read_text())
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("data"), list):
            raise ValueError("Artificial Analysis fixture is not a models response")
        age = None
        skipped = False
    else:
        directory = snapshot_dir()
        path, snapshot = _newest_snapshot(directory)
        age = _age_seconds(snapshot, now) if snapshot is not None else None
        skipped = snapshot is not None and age is not None and 0 <= age < FETCH_INTERVAL_SECONDS and not force
        if skipped:
            latest = directory / "latest.json"
            if not latest.is_file() or _read_snapshot(latest) != snapshot:
                latest.write_text(json.dumps(snapshot, indent=2) + "\n")
        else:
            key = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "")
            if not key:
                raise ValueError("ARTIFICIAL_ANALYSIS_API_KEY is not set")
            snapshot = fetch_models(key)
            snapshot["fetched_at"] = now.isoformat().replace("+00:00", "Z")
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / now.strftime("%Y%m%dT%H%M%S%fZ.json")
            content = json.dumps(snapshot, indent=2) + "\n"
            path.write_text(content)
            (directory / "latest.json").write_text(content)
            age = 0.0
    result = summarize(snapshot)
    result.update(snapshot_path=str(path), age_seconds=age, skipped=skipped)
    return result


def evidence(alias: str) -> list[dict[str, Any]] | None:
    """Read fresh AA evidence for an alias, or None when unavailable or stale."""
    snapshot = _read_snapshot(snapshot_dir() / "latest.json")
    if snapshot is None:
        return None
    age = _age_seconds(snapshot, datetime.now(timezone.utc))
    if age is None or not 0 <= age <= EVIDENCE_MAX_AGE_SECONDS:
        return None
    return summarize(snapshot).get(alias)
