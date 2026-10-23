from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

from claude_lb_certs import LbActivity, ensure_mitm_certs  # pyrefly: ignore[missing-import]
from claude_lb_common import (  # pyrefly: ignore[missing-import]
    CCGPT_MODE,
    LEAF_CERT,
    LEAF_KEY,
    SHIM_START_TIMEOUT_DEFAULT,
    LaunchConfig,
    activity_path,
    env_float,
    proxy_ready_path,
    raise_fd_soft_limit,
)
from claude_lb_tags import tag_headers  # pyrefly: ignore[missing-import]
from claude_lb_tunnel import (  # pyrefly: ignore[missing-import]
    ProxyHandler,
    ThreadingProxyServer,
    tunnel_bucket_from_env,
)


def run_lb_proxy(
    ready_path: str,
    session_id: str,
    parent_pid: int | None,
    upstream_base_url: str,
    listen_port: int = 0,
    *,
    shared: bool = False,
) -> None:
    if not shared and parent_pid is None:
        raise ValueError("parent_pid is required for a launcher-scoped proxy")
    raise_fd_soft_limit()
    ensure_mitm_certs()
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(certfile=str(LEAF_CERT), keyfile=str(LEAF_KEY))

    server = ThreadingProxyServer(("127.0.0.1", listen_port), ProxyHandler)
    server.tls_context = tls_context
    server.tunnel_bucket = tunnel_bucket_from_env()
    server.session_id = session_id
    server.parent_pid = parent_pid
    server.shared = shared
    server.tag_headers = tag_headers(os.environ) if not shared else {}
    server.ccgpt_mode = CCGPT_MODE and not shared
    server.upstream_base_url = upstream_base_url.rstrip("/")
    server.activity = (
        LbActivity(activity_path(session_id, parent_pid)) if not shared and parent_pid is not None else None
    )
    if shared:
        server.client_activity = {}
        server.client_activity_lock = threading.Lock()
    port = server.server_address[1]

    ready = Path(ready_path)
    tmp = ready.with_name(ready.name + ".tmp")
    tmp.write_text(f"{port}\n")
    os.replace(tmp, ready)

    def stop_when_parent_exits() -> None:
        assert parent_pid is not None
        while True:
            try:
                os.kill(parent_pid, 0)
            except OSError:
                server.shutdown()
                return
            time.sleep(2.0)

    if not shared:
        threading.Thread(target=stop_when_parent_exits, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        for path in (ready, server.activity.path if server.activity is not None else None):
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass


def proxy_port_ready(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


SHIM_READY_FILE_MAX_AGE_SECONDS = 24 * 3600


SHIM_READY_FILE_DEAD_GRACE_SECONDS = 10


def prune_stale_shim_files(shim_dir: Path, *, now: float | None = None) -> int:

    current_time = time.time() if now is None else now
    old_cutoff = current_time - SHIM_READY_FILE_MAX_AGE_SECONDS
    dead_cutoff = current_time - SHIM_READY_FILE_DEAD_GRACE_SECONDS
    removed = 0
    try:
        entries = list(os.scandir(shim_dir))
    except OSError:
        return 0
    for entry in entries:
        name = entry.name
        if not (name.endswith(".proxy") and (name.startswith("cc-") or name.startswith("rails-desktop-"))):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            mtime = entry.stat(follow_symlinks=False).st_mtime
            if mtime >= old_cutoff:
                if mtime >= dead_cutoff:
                    continue
                try:
                    port = int(Path(entry.path).read_text().strip())
                except ValueError:
                    port = 0
                if 1 <= port <= 65535 and proxy_port_ready(port, timeout=0.2):
                    continue
            os.unlink(entry.path)
            removed += 1
        except OSError:
            continue
    return removed


def start_lb_proxy(session_id: str) -> str:
    parent_pid = os.getpid()

    ready = proxy_ready_path(f"{session_id}.{parent_pid}")
    prune_stale_shim_files(ready.parent)
    for path in (ready, activity_path(session_id, parent_pid)):
        try:
            path.unlink()
        except OSError:
            pass
    spawn_lb_proxy(ready, session_id, parent_pid, LaunchConfig.base_url)
    deadline = time.monotonic() + env_float("CLAUDE_LB_SHIM_START_TIMEOUT", SHIM_START_TIMEOUT_DEFAULT)
    while time.monotonic() < deadline:
        try:
            text = ready.read_text().strip()
        except OSError:
            text = ""
        if text:
            try:
                port = int(text)
            except ValueError:
                port = 0
            if port and proxy_port_ready(port):
                return f"http://127.0.0.1:{port}"
        time.sleep(0.01)
    raise RuntimeError("Claude LB intercepting proxy did not start")


def spawn_lb_proxy(ready: Path, session_id: str, parent_pid: int, upstream_base_url: str) -> None:
    command = [
        sys.executable,
        str(Path(__file__).resolve().with_name("claude-lb-launch")),
        "--lb-proxy",
        str(ready),
        session_id,
        str(parent_pid),
        upstream_base_url,
    ]
    subprocess.Popen(
        command,
        env={**os.environ, "CLAUDE_LB_CODEX_MODE": "1"} if CCGPT_MODE else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
