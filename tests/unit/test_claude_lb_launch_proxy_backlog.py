from __future__ import annotations

import http.server
import socket

from tests.unit.test_claude_lb_launch import load_launcher_module

# Scenario (Opus, 2026-10-01; the implementer does not edit this file).
# 13:02Z: the shared desktop proxy on :2458 refused connections under Studio
# load (gh timeouts, review_pr crashes). It carries 60+ tunnels; a burst of
# new connections arriving while its accept thread is slow must queue, not be
# refused.


def test_proxy_queues_a_connection_burst_before_accept() -> None:
    launcher = load_launcher_module()
    server = launcher._ThreadingProxyServer(
        ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler
    )
    port = server.server_address[1]
    clients: list[socket.socket] = []
    refused = 0
    try:
        # Nothing calls accept(): every connection waits in the listen backlog.
        for _ in range(64):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(0.3)
            try:
                client.connect(("127.0.0.1", port))
            except OSError:
                refused += 1
                client.close()
                continue
            clients.append(client)
    finally:
        for client in clients:
            client.close()
        server.server_close()
    assert refused == 0, f"{refused} of 64 connections refused before accept"
