from __future__ import annotations

import os
import socket
from ipaddress import ip_address
from pathlib import Path

from dotenv import dotenv_values

from app.core.utils.proxy_env import outbound_proxy_env_configured


def _effective_environ() -> dict[str, str | None]:
    environ: dict[str, str | None] = {}
    for env_file in ENV_FILES:
        environ.update(dotenv_values(env_file))
    environ.update(os.environ)
    return environ


BASE_DIR = Path(__file__).resolve().parents[3]
ENV_FILES = (BASE_DIR / ".env", BASE_DIR / ".env.local")
DOCKER_DATA_DIR = Path("/var/lib/agent-lb")
DOCKER_CALLBACK_HOST = "0.0.0.0"


def _in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _default_home_dir() -> Path:
    env_dir = os.getenv("AGENT_LB_DATA_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip())
    home_dir = Path.home() / ".agent-lb"
    if home_dir.exists():
        return home_dir
    if _in_container():
        return DOCKER_DATA_DIR
    return home_dir


def _default_oauth_callback_host() -> str:
    if _in_container():
        return DOCKER_CALLBACK_HOST
    return "127.0.0.1"


def _default_http_bridge_instance_id() -> str:
    hostname = socket.gethostname().strip()
    return hostname or "agent-lb"


def _default_local_instance_id() -> str:
    hostname = socket.gethostname().strip().lower()
    return hostname or "agent-lb"


def _default_upstream_websocket_trust_env() -> bool:
    return outbound_proxy_env_configured(_effective_environ())


def _configured_outbound_proxy_env() -> dict[str, str | None]:
    environ: dict[str, str | None] = {}
    for env_file in ENV_FILES:
        environ.update(dotenv_values(env_file))
    environ.update(os.environ)
    return environ


DEFAULT_HOME_DIR = _default_home_dir()
DEFAULT_DB_PATH = DEFAULT_HOME_DIR / "store.db"
DEFAULT_ENCRYPTION_KEY_FILE = DEFAULT_HOME_DIR / "encryption.key"
DEFAULT_CONVERSATION_ARCHIVE_DIR = DEFAULT_HOME_DIR / "conversation-archive"
DEFAULT_DATABASE_URL = f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}"


def bridge_advertise_hostname_is_replica_specific(
    hostname: str, *, instance_id: str, multi_replica_intent: bool = False
) -> bool:
    pod_ip = os.getenv("POD_IP")
    if pod_ip and hostname == pod_ip:
        return True
    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        labels = set(hostname.split("."))
        pod_name = os.getenv("POD_NAME", "").strip()
        host_name = os.getenv("HOSTNAME", "").strip()
        allowed_labels = {
            label for label in {instance_id.strip(), pod_name, host_name, socket.gethostname().strip()} if label
        }
        return bool(labels & allowed_labels)
    return parsed_ip.is_loopback and (not multi_replica_intent)
