from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from websockets.exceptions import WebSocketException

from app.core.browser_profiles import BrowserProfile
from app.core.cdp_page import CdpError, CdpPage, close_browser
from app.core.profile_launch import LaunchError, start_chrome
from app.core.profile_lock import profile_held
from app.lb_service import (
    ServiceError,
    ServiceUnavailable,
    account_for,
    close_stale_callbacks,
    request,
    retrying,
    send_code,
    shown_code,
)
from app.reauth_enroll import refusal
from app.reauth_page import classify, click, signed_in_as

RUN_SECONDS = 180
UNKNOWN_SECONDS = 30
MAX_CLICKS = 2


@dataclass
class Outcome:
    result: str
    detail: str = ""
    clicks: list[str] = field(default_factory=list)
    bound: bool = False


def _status(base: str, flow: dict) -> dict:
    try:
        reply = request(base, "/api/oauth/status?" + urlencode({"flowId": flow["flowId"]}))
    except ServiceError:
        return {}
    return reply if isinstance(reply, dict) else {}


def _finish(base: str, profile: BrowserProfile, account_id: str, flow: dict) -> Outcome | None:
    status = _status(base, flow)
    if status.get("status") == "error":
        message = str(status.get("errorMessage") or "OAuth failed")
        kind = "stuck:wrong_account" if "started for" in message else "error"
        return Outcome(kind, message[:200])
    if status.get("status") != "success":
        return None
    account = retrying(lambda: account_for(base, profile.email, "anthropic"), "account lookup")
    if not account or account.get("accountId") != account_id:
        return Outcome("error", "sign-in finished but the account did not match")
    return Outcome("active" if account.get("status") == "active" else "error", f"status {account.get('status')}")


def _consent(page: CdpPage, profile: BrowserProfile, hold: str | None) -> Outcome | None:
    who = signed_in_as(page)
    if who is None:
        return Outcome("stuck:identity_unknown", "the consent page did not say which account is signed in")
    if who != profile.email:
        return Outcome("stuck:wrong_account", f"consent page is signed in as {who}")
    if hold == "bind_not_live":
        return Outcome(hold, f"consent reached as {who}; no Authorize click until the server binds flows")
    if hold:
        return Outcome(hold, f"consent reached as {who}; observe only")
    return None


def _drive(base: str, profile: BrowserProfile, account_id: str, flow: dict, hold: str | None) -> Outcome:
    page = CdpPage(profile.cdp_port, flow["authorizationUrl"])
    page.call("Emulation.setFocusEmulationEnabled", {"enabled": True}, session=page.session)
    page.call("Page.bringToFront", session=page.session)
    clicks: list[str] = []
    sent = False
    started = time.monotonic()
    unknown_since: float | None = None
    try:
        while time.monotonic() - started < RUN_SECONDS:
            shown = None if sent else shown_code(profile.cdp_port, flow)
            if shown and hold:
                return Outcome(hold, "Claude showed the code while holding; not sent", clicks)
            if shown:
                sent = send_code(base, flow, shown)
                shown = None
            done = _finish(base, profile, account_id, flow)
            if done:
                done.clicks = clicks
                if sent:
                    done.detail = f"{done.detail}; sent the code Claude showed on its page"
                if done.result == "active":
                    page.close()
                return done
            verdict = classify(page, profile)
            if verdict.kind.startswith("stuck:"):
                return Outcome(verdict.kind, verdict.detail, clicks)
            if verdict.kind == "consent":
                blocked = _consent(page, profile, hold)
                if blocked:
                    blocked.clicks = clicks
                    return blocked
            if verdict.kind in ("chooser", "google_button", "consent"):
                unknown_since = None
                if clicks.count(verdict.kind) >= MAX_CLICKS:
                    return Outcome("stuck:repeat", f"{verdict.kind} came back after {MAX_CLICKS} clicks", clicks)
                stopped = click(page, profile, verdict.kind)
                if stopped:
                    return Outcome(
                        stopped.kind if stopped.kind.startswith("stuck:") else "stuck:unknown", stopped.detail, clicks
                    )
                clicks.append(verdict.kind)
                time.sleep(2)
                continue
            if verdict.kind == "unknown":
                unknown_since = unknown_since or time.monotonic()
                if time.monotonic() - unknown_since > UNKNOWN_SECONDS:
                    return Outcome("stuck:unknown", verdict.detail, clicks)
            else:
                unknown_since = None
            time.sleep(1)
        return Outcome("stuck:timeout", f"no result in {RUN_SECONDS} s", clicks)
    finally:
        page.detach()


def reauth_account(base: str, account: dict, profile: BrowserProfile, *, observe_only: bool = False) -> Outcome:
    reason = refusal(account)
    if reason:
        return Outcome("skipped", reason)
    account_id = str(account["accountId"])
    if profile.email != str(account.get("email", "")).lower():
        return Outcome("skipped", "profile email does not match the account")
    with profile_held(profile.email) as free:
        if not free:
            return Outcome("busy", "another reauth, recard or auto run holds this profile")
        body = {"provider": "anthropic", "forceMethod": "browser", "expectedAccountId": account_id}
        try:
            flow = retrying(lambda: request(base, "/api/oauth/start", body=body), "oauth start")
        except (ServiceError, ServiceUnavailable) as exc:
            return Outcome("error", f"oauth start: {exc}")
        if (
            not isinstance(flow, dict)
            or not flow.get("flowId")
            or not flow.get("authorizationUrl")
            or not flow.get("callbackUrl")
        ):
            return Outcome("error", "service returned an incomplete browser OAuth flow")
        bound = flow.get("expectedAccountId") == account_id
        launched = False
        try:
            launched = start_chrome(profile, seconds=15)
            close_stale_callbacks(f"http://127.0.0.1:{profile.cdp_port}", flow["callbackUrl"])
            hold = "bind_not_live" if not bound else "observed" if observe_only else None
            outcome = _drive(base, profile, account_id, flow, hold)
        except LaunchError as exc:
            outcome = Outcome("error", f"launch: {str(exc)[:300]}")
        except (CdpError, WebSocketException, OSError, TimeoutError) as exc:
            outcome = Outcome("error", f"browser: {str(exc)[:160] or type(exc).__name__}")
        outcome.bound = bound
        if launched and not outcome.result.startswith("stuck:"):
            close_browser(profile.cdp_port)
        return outcome
