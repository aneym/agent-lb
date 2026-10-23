from __future__ import annotations

IGNORE_INBOUND_HEADERS = {
    "authorization",
    "x-api-key",
    "chatgpt-account-id",
    "content-length",
    "host",
    "forwarded",
    "x-real-ip",
    "true-client-ip",
    "x-agent-lb-machine",
    "x-agent-lb-intent",
    "x-agent-lb-lane",
    "x-agent-lb-seat",
    "x-agent-lb-room",
    "x-agent-lb-job",
    "x-agent-lb-dispatch",
    "x-factory-project",
    "x-factory-job",
    "x-factory-owner",
    "x-factory-host",
}
