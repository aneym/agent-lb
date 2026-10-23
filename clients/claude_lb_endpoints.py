from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from claude_lb_common import (  # pyrefly: ignore[missing-import]
    CCGPT_MODEL,
    LaunchConfig,
    env_float,
    has_permission_option,
)
from claude_lb_network import (  # pyrefly: ignore[missing-import]
    lb_candidates,
    lb_json,
    probe_interactive_ready,
    wait_for_local_startup,
)
from claude_lb_quota import (  # pyrefly: ignore[missing-import]
    DEFERRED_CLAIM,
    account_pressure,
    anthropic_quota_key,
    c,
    claim_at_endpoint,
    requested_effort,
    requested_model,
    short_account_name,
    wait_max_seconds,
)
from claude_lb_tags import stand_in_banner  # pyrefly: ignore[missing-import]


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

    LaunchConfig.base_url = selected_url
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
        LaunchConfig.base_url = url
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
        LaunchConfig.base_url = url
        endpoint = urllib.parse.urlsplit(url).netloc or url
        suffix = " (local)" if label == "local" else ""
        print(
            f"ccgpt: {c('Claude Code + Codex', '1;36')} · {CCGPT_MODEL}/{requested_effort(sys.argv[1:])} · "
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
