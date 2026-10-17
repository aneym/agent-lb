from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from app.status_cli import DEFAULT_BASE_URL, StatusObservationError, _NoRedirect, _validated_base_url

if TYPE_CHECKING:
    from app.modules.accounts.claude_reset_policy import PolicyState, WindowSample


class ResetCliError(Exception):
    pass


def run(args: Any) -> None:
    if args.resets_command == "policy":
        _run_policy(args)
        return
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
        account
        for account in accounts
        if account.get("accountId") == selector
        or isinstance(account.get("email"), str)
        and account["email"].casefold() == selector.casefold()
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


def _run_policy(args: Any) -> None:
    from datetime import UTC, datetime

    from pydantic import ValidationError

    from app.modules.accounts.claude_reset_policy import PolicyState, decide

    try:
        base_url = None
        if args.state:
            state = PolicyState.model_validate_json(Path(args.state).read_text())
        else:
            base_url = _validated_base_url(args.base_url or os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL))
            state = _observe_policy(base_url, args.timeout, Path(args.samples).expanduser())
        decision = decide(state, datetime.now(UTC))
        record = decision.model_dump()
        record["observed_at"] = datetime.now(UTC).isoformat()
        record["synthetic"] = bool(args.state)
        if args.apply and not args.state and decision.action == "redeem":
            assert base_url is not None and decision.account_id is not None
            path = f"/api/accounts/{quote(decision.account_id, safe='')}/rate-limit-reset-credits/consume"

            outcome = _request(base_url, path, args.timeout, body={"creditId": decision.grant_id})
            record["redemption"] = outcome
            if not isinstance(outcome, dict) or outcome.get("status") != "redeemed":
                print(json.dumps(record))
                raise SystemExit(1)
        if args.json:
            print(json.dumps(record))
        elif decision.action == "redeem":
            print(f"redeem {decision.account_id} {decision.grant_id} ({decision.reason})")
        else:
            print(f"hold {decision.reason}")
    except (OSError, ValueError, ValidationError, ResetCliError, StatusObservationError) as exc:
        message = str(exc) if isinstance(exc, (ResetCliError, StatusObservationError)) else "invalid policy state"
        print(f"agent-lb resets policy: {message}", file=sys.stderr)
        raise SystemExit(2) from exc


def _observe_policy(base_url: str, timeout: float, samples_path: Path) -> PolicyState:

    from datetime import UTC, datetime, timedelta

    from pydantic import TypeAdapter

    from app.modules.accounts.claude_reset_policy import (
        BlockedAccount,
        CodexSummary,
        LiveAccount,
        PolicyState,
        WindowSample,
    )
    from app.status_cli import observe

    now = datetime.now(UTC)
    adapter = TypeAdapter(list[WindowSample])
    samples = adapter.validate_json(samples_path.read_text()) if samples_path.exists() else []
    samples = [sample for sample in samples if now - timedelta(hours=48) <= sample.observed_at <= now]
    observation = observe(base_url=base_url, timeout=timeout, provider=None, model=None)
    current_samples = []
    for account in observation["accounts"]:
        if account["provider"] != "anthropic":
            continue
        for window in ("primary", "weekly"):
            quota = account[window]
            if quota["remaining_percent"] is not None and quota["reset_at"] is not None:
                current_samples.append(
                    WindowSample(
                        account_id=account["account_id"],
                        window=window,
                        reset_at=quota["reset_at"],
                        remaining_pct=quota["remaining_percent"],
                        observed_at=now,
                    )
                )
    _save_policy_samples(samples_path, samples + current_samples, adapter)
    live = []
    blocked = []
    codex = CodexSummary()
    for account in observation["accounts"]:
        if account["provider"] == "openai":
            codex.usable_count += int(account["usable"] == "usable")
            codex.banked_resets_total += account["reset_credits_available"] or 0
            continue
        if account["provider"] != "anthropic":
            continue
        account_id = account["account_id"]
        if account["usable"] == "unknown":
            raise ResetCliError("Claude quota telemetry is incomplete; no redemption attempted")
        if account["usable"] != "usable":
            if account["status"] not in {"active", "rate_limited", "quota_exceeded"}:
                continue
            if account["subscription"]["usable"] is False:
                continue
            resets = [
                datetime.fromisoformat(value)
                for value in (
                    account["primary"]["reset_at"],
                    account["weekly"]["reset_at"],
                    account["rate_limit_reset_at"],
                )
                if value
            ]
            future = [reset for reset in resets if reset > now]
            if future:
                blocked.append(BlockedAccount(id=account_id, reset_at=min(future)))
            continue
        burns = []
        valid = True
        for window in ("primary", "weekly"):
            current = account[window]
            if current["remaining_percent"] is None:
                continue
            reset_at = datetime.fromisoformat(current["reset_at"]) if current["reset_at"] else None
            prior = max(
                (
                    sample
                    for sample in samples
                    if sample.account_id == account_id
                    and sample.window == window
                    and sample.reset_at == reset_at
                    and sample.observed_at <= now - timedelta(minutes=5)
                ),
                key=lambda sample: sample.observed_at,
                default=None,
            )
            if prior is None or prior.remaining_pct < current["remaining_percent"]:
                valid = False
            else:
                hours = (now - prior.observed_at).total_seconds() / 3600
                burns.append((prior.remaining_pct - current["remaining_percent"]) / hours)
        live.append(
            LiveAccount(
                id=account_id,
                usable=True,
                primary_remaining_pct=account["primary"]["remaining_percent"],
                primary_reset_at=account["primary"]["reset_at"],
                weekly_remaining_pct=account["weekly"]["remaining_percent"],
                weekly_reset_at=account["weekly"]["reset_at"],
                burn_rate_pct_per_hour=max(burns) if valid and burns else None,
            )
        )
    if live and all(account.remaining() is not None and account.remaining() < 5 for account in live):
        for account in live:
            try:
                inventory = _inventory(
                    _request(
                        base_url,
                        f"/api/accounts/{quote(account.id, safe='')}/rate-limit-reset-credits",
                        timeout,
                    )
                )
            except ResetCliError:
                account.inventory_available = False
                break
            grant = next((credit for credit in inventory["credits"] if credit.get("status") == "available"), {})
            account.reset_grants_available = inventory["availableCount"]
            account.next_grant_id = grant.get("id")
            expires_at = grant.get("expiresAt")
            account.grant_expires_at = datetime.fromisoformat(expires_at) if expires_at else None
            account.redeemable_now = inventory.get("redeemableNow") is True
    return PolicyState(live_accounts=live, blocked_accounts=blocked, codex=codex)


def _save_policy_samples(path: Path, samples: list[WindowSample], adapter: Any) -> None:
    from tempfile import NamedTemporaryFile

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(adapter.dump_json(samples))
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
