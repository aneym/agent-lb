from __future__ import annotations

import os
from pathlib import Path

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 2456
DEFAULT_DATA_DIR = Path.home() / ".agent-lb-solo"
POOL_DATA_DIR = Path.home() / ".agent-lb"
MARKER_ENV = "AGENT_LB_SOLO"

_BLANKED = (
    "AGENT_LB_FEDERATION_TOKEN",
    "AGENT_LB_FEDERATION_MIRROR_TOKEN",
    "AGENT_LB_FEDERATION_TRANSFER_TOKEN",
    "AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256",
    "AGENT_LB_FEDERATION_TRANSFER_OUTBOUND_TOKEN",
    "AGENT_LB_FEDERATION_PEER_URL",
    "AGENT_LB_FEDERATION_TAILSCALE_BIN",
)
_DROPPED = (
    "AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS",
    "AGENT_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING",
    "AGENT_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL",
)


class SoloRefused(SystemExit):
    pass


def resolve_data_dir(raw: str | None) -> Path:
    chosen = raw or os.environ.get("AGENT_LB_SOLO_DATA_DIR") or str(DEFAULT_DATA_DIR)
    path = Path(chosen).expanduser().resolve()
    if path == POOL_DATA_DIR.resolve():
        raise SoloRefused(f"one-person mode refuses {path}: that is the shared agent-lb data dir; pick another")
    return path


def apply(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in _DROPPED:
        os.environ.pop(name, None)
    for name in _BLANKED:
        os.environ[name] = ""
    os.environ.update(
        {
            MARKER_ENV: "1",
            "AGENT_LB_DATA_DIR": str(data_dir),
            "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{data_dir / 'store.db'}",
            "AGENT_LB_ENCRYPTION_KEY_FILE": str(data_dir / "encryption.key"),
            "AGENT_LB_STATE_DIR": str(data_dir / "state"),
            "AGENT_LB_THROTTLE_FILE": str(data_dir / "state" / "upload-throttle.json"),
            "AGENT_LB_CONVERSATION_ARCHIVE_DIR": str(data_dir / "conversation-archive"),
            "AGENT_LB_FEDERATION_PUSH_PATH": str(data_dir / "federation-off" / "push.json"),
            "AGENT_LB_FEDERATION_PUSH_SOURCES_PATH": str(data_dir / "federation-off" / "push-sources.json"),
            "AGENT_LB_METRICS_ENABLED": "false",
            "AGENT_LB_OAUTH_CALLBACK_HOST": LOOPBACK_HOST,
            "AGENT_LB_OTEL_ENABLED": "false",
        }
    )
