from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _relay_key() -> str:
    given = os.environ.get("MODEL_DOOR_RELAY_KEY", "").strip()
    if given:
        return given
    name = os.environ.get("MODEL_DOOR_RELAY_SECRET", "rails-one-relay-key")
    found = subprocess.run(["agent-secret", "get", name], capture_output=True, text=True, timeout=20, check=True)
    key = found.stdout.strip()
    if not key:
        raise RuntimeError(f"agent-secret {name} is empty")
    return key


@dataclass(frozen=True)
class DoorConfig:
    host: str
    port: int
    rails: str
    relay_key: str
    upstream: str
    upstream_token: str
    seat: str
    pass_ttl_s: float
    log_path: Path

    @classmethod
    def from_env(cls) -> DoorConfig:
        env = os.environ.get
        return cls(
            host=env("MODEL_DOOR_HOST", "127.0.0.1"),
            port=int(env("MODEL_DOOR_PORT", "2472")),
            rails=env("MODEL_DOOR_RAILS", "https://one.rails.so").rstrip("/"),
            relay_key=_relay_key(),
            upstream=env("MODEL_DOOR_UPSTREAM", "http://127.0.0.1:2456").rstrip("/"),
            upstream_token=env("MODEL_DOOR_UPSTREAM_TOKEN", "factory-box-placeholder"),
            seat=env("MODEL_DOOR_SEAT", "rails-box"),
            pass_ttl_s=float(env("MODEL_DOOR_PASS_TTL_S", "30")),
            log_path=Path(env("MODEL_DOOR_LOG", str(Path.home() / ".agent-lb-door" / "door.jsonl"))),
        )
