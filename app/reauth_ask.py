from __future__ import annotations

import os
import subprocess
import time
from datetime import date
from urllib.parse import quote

from app.core import rails_ask
from app.core.browser_profiles import BrowserProfile, user_data_dir
from app.core.profile_theme import profile_name
from app.core.thread_delivery import send

ASKER = "factory"
ASK_GAP_S = 24 * 3600
DASHBOARD = os.getenv("AGENT_LB_DASHBOARD_URL", "https://ax42.tailf266ac.ts.net:8455").rstrip("/")

WHY = {
    "stuck:password": "it stopped at a password prompt",
    "stuck:code": "it stopped at a verification code prompt",
    "stuck:passkey": "it stopped at a passkey prompt",
    "stuck:captcha": "it stopped at a captcha",
    "stuck:verify": "Google asked to verify it's you",
    "stuck:email_login": "claude.ai wants an emailed sign-in code",
    "stuck:signed_out": "the window is signed out of claude.ai",
    "stuck:google_signed_out": "the window is signed out of Google",
    "stuck:wrong_account": "the window is signed in as a different account",
    "stuck:identity_unknown": "it could not confirm which account the consent page was for",
    "stuck:repeat": "the same page kept coming back",
    "stuck:unknown": "it reached a page it does not know",
    "stuck:timeout": "the sign-in did not finish in three minutes",
    "bind_not_live": "automatic renewal waits for the next agent-lb restart",
    "lapsed:google": "the weekly check found its Google session signed out",
    "lapsed:claude": "the weekly check found its claude.ai session signed out",
}


def question(email: str, account_id: str, kind: str, profile: BrowserProfile) -> tuple[str, list[str]]:
    window = profile_name(user_data_dir(profile)) or profile.label or email
    lines = [
        f"Sign in to Claude as {email}",
        f"agent-lb could not renew this sign-in by itself: {WHY.get(kind, 'it stopped')}.",
        f'Open {DASHBOARD}/reauth/{quote(account_id, safe="")} to sign in using the isolated server profile "{window}".',
        "If Claude blocks the server IP, use the private sign-in instructions on that page.",
    ]
    return "\n".join(lines), ["Signed in on the server", "Used private sign-in"]


def signin_lane() -> str | None:
    return os.getenv("AGENT_LB_SIGNIN_LANE", "").strip() or None


def ask_hold(entry: dict, kind: str, now: float) -> str | None:
    if entry.get("askId"):
        return f"ask already open ({entry['askId']})"
    if kind in entry.get("closedCauses", []):
        return "the owner cleared this ask; no new one until the account changes"
    if now - float(entry.get("askedAt") or 0) < ASK_GAP_S:
        return "asked in the last day"
    return None


def file_ask(entry: dict, email: str, account_id: str, kind: str, profile: BrowserProfile, *, dry_run: bool) -> str:
    now = time.time()
    if hold := ask_hold(entry, kind, now):
        return hold
    text, options = question(email, account_id, kind, profile)
    if dry_run:
        entry["askId"] = "dry-run"
        entry["askKind"] = kind
        where = f"floor account; lane {signin_lane() or 'none'}"
        return "would file ask: " + text.splitlines()[0] + f" ({WHY.get(kind, kind)}; {where})"
    landed = rails_ask.ask_owner(ASKER, text, options, floor="account", lane=signin_lane())
    if not landed.get("landed"):
        return f"ask not filed: {landed.get('detail')}"
    entry["askId"] = landed["askId"]
    entry["askKind"] = kind
    entry["askedAt"] = now
    return f"{'kept open' if landed.get('repeat') else 'filed'} ask {landed['askId']}"


def closure(entry: dict) -> str | None:
    ask_id = entry.get("askId")
    if not ask_id or ask_id == "dry-run":
        return None
    ask = rails_ask.ask_state(ASKER, ask_id)
    if ask is None or rails_ask.is_open(ask):
        return None
    entry.pop("askId", None)
    kind = entry.pop("askKind", None)
    causes = entry.setdefault("closedCauses", [])
    if kind and kind not in causes:
        causes.append(kind)
    answer = ask.get("answer") if isinstance(ask.get("answer"), dict) else {}
    return "answered" if answer.get("choice") and not answer.get("not_needed") else "cleared"


def changed(entry: dict, *, lapsed: bool) -> None:
    causes = [cause for cause in entry.get("closedCauses", []) if cause.startswith("lapsed:") != lapsed]
    if causes:
        entry["closedCauses"] = causes
    else:
        entry.pop("closedCauses", None)


def withdraw_ask(entry: dict, *, dry_run: bool) -> str | None:
    ask_id = entry.get("askId")
    if not ask_id:
        return None
    if dry_run or ask_id == "dry-run":
        entry.pop("askId", None)
        entry.pop("askKind", None)
        return f"would withdraw ask {ask_id}"
    withdrawn = rails_ask.withdraw(ASKER, ask_id)
    if withdrawn:
        entry.pop("askId", None)
        entry.pop("askKind", None)
    return f"withdrew ask {ask_id}" if withdrawn else f"could not withdraw ask {ask_id}"


def note_error(entry: dict, message: str, *, dry_run: bool) -> str:
    today = date.today().isoformat()
    if entry.get("errorNotedOn") == today:
        return "error already noted today"
    if dry_run:
        return "would note the error to Factory"
    try:
        result = send(ASKER, f"agent-lb auto-reauth error: {message}", mode="queue")
        landed = result.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        landed = False
    if landed:
        entry["errorNotedOn"] = today
    return "noted the error to Factory" if landed else "could not reach Factory's thread"
