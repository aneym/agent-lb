from __future__ import annotations

import re
import signal
from datetime import timedelta
from typing import TypeVar

VENDORS = ("cursor", "devin", "muse")


MUSE_PIN = "Muse Code 1.4.9 (1.4.9-R6297.1)"


MUSE_MIN = (1, 4, 9)


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
