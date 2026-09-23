"""Explicit reset-credit operator commands against the local service."""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from app.status_cli import DEFAULT_BASE_URL, StatusObservationError, _NoRedirect, _validated_base_url


class ResetCliError(Exception):
    pass


def run(args: Any) -> None:
    try:
        base_url = _validated_base_url(args.base_url or os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL))
        account = _select_account(_request(base_url, "/api/accounts", args.timeout), args.account)
        account_id = account["accountId"]
        inventory_path = f"/api/accounts/{quote(account_id, safe='')}/rate-limit-reset-credits"
        inventory = _inventory(_request(base_url, inventory_path, args.timeout))
        _print_inventory(account, inventory)
        if args.resets_command == "list":
            return
        if inventory["availableCount"] == 0:
            raise ResetCliError("no banked reset credits; no redemption attempted")
        if inventory.get("redeemableNow") is False:
            reason = inventory.get("ineligibleReason") or "provider reports not eligible now"
            raise ResetCliError(f"not currently redeemable: {reason}; no redemption attempted")
        credit_id = args.credit_id
        if credit_id is not None and not any(credit.get("id") == credit_id for credit in inventory["credits"]):
            raise ResetCliError(f"credit ID {credit_id!r} is not in the live inventory; no redemption attempted")
        if not args.yes and not _confirm(account):
            raise ResetCliError("redemption not confirmed; no redemption attempted")
        body = {"creditId": credit_id} if credit_id is not None else {}
        result = _request(base_url, inventory_path + "/consume", args.timeout, body=body)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("status"), str)
            or not isinstance(result.get("code"), str)
        ):
            raise ResetCliError("service returned a malformed redemption response")
        windows_reset = result.get("windowsReset", "unknown")
        print(f"Redemption: {result['status']} (code: {result['code']}; windows reset: {windows_reset})")
        if result["status"] != "redeemed":
            raise SystemExit(1)
    except (ResetCliError, StatusObservationError) as exc:
        print(f"agent-lb resets: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _select_account(response: Any, selector: str) -> dict[str, Any]:
    accounts = response.get("accounts") if isinstance(response, dict) else None
    if not isinstance(accounts, list) or not all(isinstance(item, dict) for item in accounts):
        raise ResetCliError("service returned a malformed accounts response")
    matches = [
        account for account in accounts
        if account.get("accountId") == selector
        or isinstance(account.get("email"), str) and account["email"].casefold() == selector.casefold()
    ]
    if len(matches) != 1:
        raise ResetCliError(f"account {selector!r} matched {len(matches)} accounts; use a unique exact ID or email")
    if not isinstance(matches[0].get("accountId"), str) or not matches[0]["accountId"]:
        raise ResetCliError("selected account has no valid ID")
    return matches[0]


def _inventory(response: Any) -> dict[str, Any]:
    if (
        not isinstance(response, dict)
        or type(response.get("availableCount")) is not int
        or response["availableCount"] < 0
        or not isinstance(response.get("credits"), list)
        or not all(isinstance(credit, dict) for credit in response["credits"])
    ):
        raise ResetCliError("service returned a malformed reset-credit inventory")
    if (
        "redeemableNow" in response
        and response["redeemableNow"] is not None
        and not isinstance(response["redeemableNow"], bool)
    ):
        raise ResetCliError("service returned malformed redemption eligibility")
    return response


def _print_inventory(account: dict[str, Any], inventory: dict[str, Any]) -> None:
    print(f"Account: {account.get('email') or account['accountId']} ({account['accountId']})")
    print(f"Banked reset credits: {inventory['availableCount']}")
    eligible = inventory.get("redeemableNow")
    label = "yes" if eligible is True else "no" if eligible is False else "unknown (banked does not mean redeemable)"
    reason = inventory.get("ineligibleReason")
    print(f"Currently redeemable: {label}{f' ({reason})' if isinstance(reason, str) and reason else ''}")
    for credit in inventory["credits"]:
        credit_id = credit.get("id", "unknown")
        status = credit.get("status", "unknown")
        expires = credit.get("expiresAt") or "unknown"
        print(f"- {credit_id} [{status}] expires {expires}")


def _confirm(account: dict[str, Any]) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"Redeem one reset credit for {account.get('email') or account['accountId']}? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().casefold() in {"y", "yes"}


def _request(base_url: str, path: str, timeout: float, *, body: dict[str, Any] | None = None) -> Any:
    headers = {"Accept": "application/json"}
    if cookie := os.getenv("AGENT_LB_STATUS_COOKIE"):
        headers["Cookie"] = cookie
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = Request(base_url + path, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=timeout) as response:  # noqa: S310 -- explicit local/operator URL.
            if not 200 <= response.status < 300:
                raise ResetCliError(f"service returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ResetCliError(f"service returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ResetCliError("service is unavailable or did not respond") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResetCliError("service returned invalid JSON") from exc
