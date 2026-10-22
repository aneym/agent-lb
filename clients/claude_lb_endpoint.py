from __future__ import annotations  # noqa: I001


import errno
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from typing import cast

from clients.claude_lb_command import anthropic_quota_key
from clients.claude_lb_command import has_permission_option
from clients.claude_lb_command import requested_effort
from clients.claude_lb_command import requested_model
from clients.claude_lb_config import CCGPT_MODEL
from clients.claude_lb_config import LOCAL_LB_URL
from clients.claude_lb_config import default_remote_url
from clients.claude_lb_config import env_float
from clients.claude_lb_config import launch_context
from clients.claude_lb_config import remote_only_mac
from clients.claude_lb_quota import account_pressure
from clients.claude_lb_quota import c
from clients.claude_lb_quota import format_lb_pick_error
from clients.claude_lb_quota import retry_at_from_error_body
from clients.claude_lb_quota import short_account_name
from clients.claude_lb_quota import should_wait_for_reset
from clients.claude_lb_quota import wait_max_seconds
from clients.claude_lb_quota import wait_until_reset
from clients.claude_lb_tags import stand_in_banner


def lb_json(path: str, *, timeout: float = 2.5, base_url: str | None = None) -> dict:
    request = urllib.request.Request(
        f"{base_url or launch_context.base_url}{path}", headers={"accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def lb_post_json(path: str, payload: dict[str, str], *, timeout: float = 4.0, base_url: str | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url or launch_context.base_url}{path}",
        data=body,
        headers={"accept": "application/json", "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_body = response.read().decode("utf-8")
    return json.loads(response_body) if response_body else {}


def is_unreachable_health_error(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return False
    reason = getattr(error, "reason", error)
    if isinstance(reason, ConnectionError):
        return True
    return isinstance(reason, OSError) and reason.errno in {
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETUNREACH,
    }


def is_timeout_error(error: BaseException) -> bool:

    if isinstance(error, urllib.error.HTTPError):
        return False
    reason = getattr(error, "reason", error)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return isinstance(reason, OSError) and reason.errno in {errno.ETIMEDOUT, errno.EAGAIN}


def probe_interactive_ready(base_url: str, *, timeout: float, retries: int, gap: float) -> tuple[str, str]:

    last_error = "no response"
    for attempt in range(max(1, retries)):
        try:
            lb_json("/health/ready", timeout=timeout, base_url=base_url)
            return "ready", ""
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = str(error) or error.__class__.__name__
            if is_timeout_error(error):
                return "busy", last_error
            if is_unreachable_health_error(error):
                return "unreachable", last_error
            if attempt + 1 < retries:
                time.sleep(gap)
    return "down", last_error


def probe_health_at(base_url: str, *, retries: int, timeout: float, gap: float) -> tuple[bool, str, bool]:

    last_error = "no response"
    last_error_unreachable = False
    for attempt in range(retries):
        try:
            lb_json("/health/ready", timeout=timeout, base_url=base_url)
            return True, "", False
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = str(error) or error.__class__.__name__
            last_error_unreachable = is_unreachable_health_error(error)
            if attempt + 1 < retries:
                time.sleep(gap)
    return False, last_error, last_error_unreachable


def local_launchd_job_loaded() -> bool:
    label = os.environ.get("CLAUDE_LB_LAUNCHD_LABEL") or "com.aneyman.agent-lb"
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
        )
    except (AttributeError, OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def wait_for_local_startup(base_url: str, *, probe_timeout: float) -> bool:
    grace_seconds = max(0.0, env_float("CLAUDE_LB_STARTUP_GRACE_SECONDS", 90.0))
    if grace_seconds == 0 or not local_launchd_job_loaded():
        return False

    print(
        f"cc: agent-lb is starting — waiting up to {grace_seconds:g}s for /health/ready ...",
        file=sys.stderr,
    )
    deadline = time.monotonic() + grace_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(1.0, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        ready, _, _ = probe_health_at(
            base_url,
            retries=1,
            timeout=min(probe_timeout, remaining),
            gap=0.0,
        )
        if ready:
            print("cc: agent-lb is ready — continuing with local LB", file=sys.stderr)
            return True


def lb_candidates() -> list[tuple[str, str]]:

    local_url = (os.environ.get("CLAUDE_LB_LOCAL_URL") or LOCAL_LB_URL).rstrip("/")
    remote_url = (os.environ.get("CLAUDE_LB_BASE_URL") or default_remote_url()).rstrip("/")
    prefer_local = os.environ.get("CLAUDE_LB_LOCAL_PREFER") != "0" and not remote_only_mac()
    prefer_remote = os.environ.get("CLAUDE_LB_PREFER_REMOTE") == "1"

    candidates: list[tuple[str, str]] = []
    if prefer_remote and remote_url != local_url:
        candidates.append(("remote", remote_url))
    if prefer_local:
        candidates.append(("local", local_url))
    if remote_url != local_url and all(url != remote_url for _, url in candidates):
        candidates.append(("remote", remote_url))
    if not candidates:
        candidates.append(("remote", remote_url))
    return candidates


DEFERRED_CLAIM: dict = {"deferred": True}


def claim_session_route(
    session_id: str, model: str, quota_key: str, base_url: str, *, timeout: float | None = None
) -> tuple[dict | None, str, int | None]:

    budget = timeout if timeout is not None else env_float("CLAUDE_LB_CLAIM_TIMEOUT", 5.0)
    for attempt in range(2):
        try:
            claim = lb_post_json(
                "/api/anthropic/session-route",
                {"sessionId": session_id, "model": model, "quotaKey": quota_key},
                timeout=budget * (attempt + 1),
                base_url=base_url,
            )
            return claim, "", None
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            return None, format_lb_pick_error(body), retry_at_from_error_body(body)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            if is_timeout_error(error) and attempt == 0:
                continue
            if is_timeout_error(error):
                return DEFERRED_CLAIM, "", None
            return None, str(error), None
    return None, "claim retries exhausted", None


def claim_at_endpoint(
    base_url: str,
    session_id: str,
    model: str,
    quota_key: str,
    deadline: float,
    request_timeout: float | None = None,
) -> tuple[dict | None, str]:

    while True:
        claim, error_message, retry_at = claim_session_route(
            session_id,
            model,
            quota_key,
            base_url,
            timeout=request_timeout,
        )
        if claim is not None:
            return claim, ""
        if not should_wait_for_reset(retry_at, deadline):
            return None, error_message
        try:
            wait_until_reset(cast(int, retry_at), error_message)
        except KeyboardInterrupt:
            print("cc: aborted while waiting for limits to reset", file=sys.stderr)
            sys.exit(130)


def print_lb_banner(argv: list[str], session_id: str) -> bool:
    model = requested_model(argv)
    effort = requested_effort(argv)
    quota_key, quota_label = anthropic_quota_key(model, effort)
    model_label = model.replace("claude-", "")

    timeout = env_float("CLAUDE_LB_HEALTH_TIMEOUT", 3.0)
    local_timeout = min(2.0, timeout)
    deadline = time.time() + wait_max_seconds()

    claim_failures: list[str] = []
    selected_url = ""
    selected_label = ""
    claim: dict | None = None
    for label, url in lb_candidates():
        request_timeout = local_timeout if label == "local" else env_float("CLAUDE_LB_CLAIM_TIMEOUT", 5.0)
        claim, claim_error = claim_at_endpoint(
            url,
            session_id,
            model,
            quota_key,
            deadline,
            request_timeout=request_timeout,
        )
        if claim is not None:
            selected_url, selected_label = url, label
            break
        claim_failures.append(f"{label} ({url}): {claim_error}")

    if claim is None:
        if claim_failures:
            print(
                f"cc: Claude LB could not pick an account for {model_label}/{quota_label}: "
                + "; ".join(claim_failures),
                file=sys.stderr,
            )
        else:
            print("cc: Claude LB unavailable (no candidates configured)", file=sys.stderr)
        return False

    launch_context.base_url = selected_url
    endpoint_note = f"via {urllib.parse.urlsplit(selected_url).netloc or selected_url}"
    if selected_label == "local":
        endpoint_note += " (local)"

    if claim is DEFERRED_CLAIM:
        print(
            f"cc: {c('Claude LB', '1;36')} · {model_label}/{quota_label} · LB busy, sticky account "
            f"selected on first message {c(endpoint_note, '2')} · {stand_in_banner()}",
            file=sys.stderr,
        )
        return True

    selected_account_id = str(claim.get("accountId") or "")

    try:
        payload = lb_json("/api/accounts", timeout=env_float("CLAUDE_LB_BANNER_TIMEOUT", 1.5))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        account_label = claim.get("alias") or claim.get("email") or selected_account_id[:8]
        print(
            f"cc: {c('Claude LB', '1;36')} → {c(account_label, '1')} · {model_label}/{quota_label} · "
            f"proxy-claimed session route {c('· OAuth/Max · sticky+failover', '2')} "
            f"{c(endpoint_note, '2')} · {stand_in_banner()}",
            file=sys.stderr,
        )
        return True

    accounts = payload.get("accounts") or []
    anthropic = [
        account
        for account in accounts
        if account.get("provider") == "anthropic" and str(account.get("status", "")).lower() == "active"
    ]
    if not anthropic:
        account_label = claim.get("alias") or claim.get("email") or selected_account_id[:8]
        print(
            f"cc: {c('Claude LB', '1;36')} → {c(account_label, '1')} · {model_label}/{quota_label} · "
            f"proxy-claimed session route {c('· OAuth/Max · sticky+failover', '2')} "
            f"{c(endpoint_note, '2')} · {stand_in_banner()}",
            file=sys.stderr,
        )
        return True

    selected_account: dict | None = None
    for account in anthropic:
        if str(account.get("accountId") or "") == selected_account_id:
            selected_account = account
            break
    selected = selected_account or {
        "accountId": selected_account_id,
        "email": claim.get("email"),
        "alias": claim.get("alias"),
    }
    _, _, _, _, _, reason = account_pressure(selected, quota_key)
    print(
        f"cc: {c('Claude LB', '1;36')} → {c(short_account_name(selected), '1')} · {model_label} · "
        f"{reason} {c('· OAuth/Max · sticky+failover', '2')} {c(endpoint_note, '2')} · {stand_in_banner()}",
        file=sys.stderr,
    )
    return True


def prepare_interactive_endpoint() -> bool:

    failures: list[str] = []

    local_timeout = env_float("CLAUDE_LB_LOCAL_READY_TIMEOUT", 1.0)
    remote_timeout = env_float("CLAUDE_LB_REMOTE_READY_TIMEOUT", 3.0)
    strict = os.environ.get("CLAUDE_LB_STRICT_READY") == "1"
    for label, url in lb_candidates():
        state, error = probe_interactive_ready(
            url,
            timeout=local_timeout if label == "local" else remote_timeout,
            retries=3,
            gap=1.0,
        )
        if label == "local" and state == "unreachable":
            if wait_for_local_startup(url, probe_timeout=local_timeout):
                state = "ready"
        if state == "busy" and strict:
            state = "down"
        if state not in {"ready", "busy"}:
            failures.append(f"{label} ({url}): {error}")
            continue
        launch_context.base_url = url
        endpoint = urllib.parse.urlsplit(url).netloc or url
        suffix = " (local)" if label == "local" else ""
        note = " · LB busy, routing anyway" if state == "busy" else ""
        print(
            f"cc: {c('Claude LB', '1;36')} · sticky account selected on first message "
            f"{c(f'via {endpoint}{suffix}{note}', '2')} · {stand_in_banner()}",
            file=sys.stderr,
        )
        return True
    print("cc: agent-lb unavailable (" + "; ".join(failures) + ")", file=sys.stderr)
    return False


def prepare_ccgpt_endpoint() -> bool:

    failures: list[str] = []
    timeout = env_float("CLAUDE_LB_HEALTH_TIMEOUT", 3.0)
    for label, url in lb_candidates():
        compatible, capability_error = probe_ccgpt_at(
            url,
            timeout=min(2.0, timeout) if label == "local" else timeout,
        )
        if not compatible:
            failures.append(f"{label} ({url}): {capability_error}")
            continue
        launch_context.base_url = url
        endpoint = urllib.parse.urlsplit(url).netloc or url
        suffix = " (local)" if label == "local" else ""
        print(
            f"ccgpt: {c('Claude Code + Codex', '1;36')} · {CCGPT_MODEL}/high · "
            f"{'bypass perms' if not has_permission_option(sys.argv[1:]) else 'custom perms'} · "
            f"requested priority {c(f'via {endpoint}{suffix}', '2')} · {stand_in_banner()}",
            file=sys.stderr,
        )
        return True
    print("ccgpt: agent-lb unavailable (" + "; ".join(failures) + ")", file=sys.stderr)
    return False


def probe_ccgpt_at(base_url: str, *, timeout: float) -> tuple[bool, str]:
    payload = json.dumps({"model": CCGPT_MODEL, "messages": []}).encode()
    request = urllib.request.Request(
        f"{base_url}/v1/ccgpt/messages/count_tokens",
        data=payload,
        headers={"content-type": "application/json", "accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return False, f"ccgpt compatibility probe failed: {error}"
    count = body.get("input_tokens") if isinstance(body, dict) else None
    if not isinstance(count, int):
        return False, "ccgpt compatibility probe returned an invalid response"
    try:
        accounts = lb_json("/api/accounts", timeout=timeout, base_url=base_url).get("accounts") or []
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return False, f"ccgpt account-pool probe failed: {error}"
    if not any(
        account.get("provider") == "openai"
        and str(account.get("status", "")).lower() == "active"
        and str(account.get("routingPolicy", "normal")).lower() not in {"paused", "disabled"}
        for account in accounts
    ):
        return False, "no active OpenAI accounts"
    return True, ""
