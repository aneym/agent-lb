from __future__ import annotations

import json
import re
from dataclasses import dataclass

from websockets.exceptions import WebSocketException

from app.core.browser_profiles import BrowserProfile
from app.core.cdp_page import CdpError, CdpPage
from app.core.claude_billing import SIGNED_IN

_FRAME = r"""(() => {
  const seen = [];
  const scan = w => {
    let d;
    try { d = w.document; if (!d || !d.body) return; } catch (e) { return; }
    const vis = e => { const r = e.getBoundingClientRect(); const s = w.getComputedStyle(e);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
    const q = sel => [...d.querySelectorAll(sel)].filter(vis).length > 0;
    seen.push({
      url: w.location.href,
      title: d.title || '',
      text: (d.body.innerText || '').slice(0, 20000),
      password: q('input[type=password]'),
      code: q('input[autocomplete=one-time-code], input[name*=code i], input[id*=code i], input[name*=totp i], input[name=pin], input[name=Pin]'),
      emailInput: q('input[type=email], input[name=identifier], input[autocomplete=username]'),
      captcha: q('iframe[src*="hcaptcha"], iframe[src*="recaptcha"], iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, .cf-turnstile, #challenge-form, #cf-challenge-running'),
    });
    for (let i = 0; i < w.frames.length; i++) scan(w.frames[i]);
  };
  scan(window);
  return seen;
})()"""

_PASSKEY = re.compile(r"passkey|security key|use your (phone|fingerprint|face|screen lock)", re.I)
_VERIFY = re.compile(
    r"verify it.?s you|confirm it.?s you|2-step verification|check your phone|google sent a notification|"
    r"try another way|confirm your recovery",
    re.I,
)
_CODE = re.compile(
    r"enter (the |a |your )?(\d-digit |verification |security |sign.?in |login )?code|we sent (a |an )?code", re.I
)
_EMAIL_LOGIN = re.compile(
    r"check your email|we sent (you )?(an email|a link|a sign.?in link)|magic link|login code", re.I
)
_CAPTCHA = re.compile(
    r"i.?m not a robot|verify you are human|are you a robot|checking your browser|just a moment|unusual traffic|"
    r"select all (images|squares)",
    re.I,
)
_AUTHORIZE = r"""(() => {
  const b = [...document.querySelectorAll('button')].find(e => e.innerText.trim() === 'Authorize' && !e.disabled);
  return Boolean(b);
})()"""
_GOOGLE_BUTTON = r"""[...document.querySelectorAll('button, a[role=button]')].find(e => /^continue with google$/i.test(e.innerText.trim()))"""


def _tile(email: str) -> str:
    quoted = json.dumps(email)
    return (
        "[...document.querySelectorAll('[data-identifier], [data-email]')]"
        f".find(e => (e.getAttribute('data-identifier') || e.getAttribute('data-email') || '').toLowerCase() === {quoted})"
    )


@dataclass(frozen=True)
class Verdict:
    kind: str
    detail: str = ""


def _frames(page: CdpPage) -> list[dict]:
    frames: list[dict] = []
    for session in [page.session, *page.frame_sessions]:
        try:
            value = page.evaluate(_FRAME, session=session)
        except (CdpError, WebSocketException, TimeoutError):
            continue
        if isinstance(value, list):
            frames.extend(row for row in value if isinstance(row, dict))
    return frames


def stuck_in(frames: list[dict]) -> Verdict | None:
    for frame in frames:
        text = f"{frame.get('title', '')}\n{frame.get('text', '')}"
        url = str(frame.get("url", ""))
        if frame.get("captcha") or _CAPTCHA.search(text) or "/cdn-cgi/challenge" in url:
            return Verdict("stuck:captcha", _bare(url))
        if frame.get("password"):
            return Verdict("stuck:password", _bare(url))
        if _PASSKEY.search(text):
            return Verdict("stuck:passkey", _bare(url))
        if _VERIFY.search(text):
            return Verdict("stuck:verify", _bare(url))
        if frame.get("code") or _CODE.search(text):
            return Verdict("stuck:code", _bare(url))
        if _EMAIL_LOGIN.search(text):
            return Verdict("stuck:email_login", _bare(url))
    return None


def _bare(url: str) -> str:
    return re.split(r"[?#]", url, maxsplit=1)[0][:120]


def _host_path(url: str) -> tuple[str, str]:
    match = re.match(r"https?://([^/:]+)(?::\d+)?(/[^?#]*)?", url)
    return (match.group(1).lower(), match.group(2) or "/") if match else ("", "/")


def _visible(page: CdpPage, expression: str) -> bool:
    script = f"(() => {{ const e = {expression}; if (!e) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }})()"
    try:
        return bool(page.evaluate(script))
    except (CdpError, WebSocketException, TimeoutError):
        return False


def signed_in_as(page: CdpPage) -> str | None:
    try:
        value = page.evaluate(SIGNED_IN)
    except (CdpError, WebSocketException, TimeoutError):
        return None
    return value.strip().lower() if isinstance(value, str) else None


def classify(page: CdpPage, profile: BrowserProfile) -> Verdict:
    frames = _frames(page)
    if not frames:
        return Verdict("loading")
    stuck = stuck_in(frames)
    if stuck:
        return stuck
    url = str(frames[0].get("url", ""))
    host, path = _host_path(url)
    text = str(frames[0].get("text", ""))
    if host == "accounts.google.com":
        if _visible(page, _tile(profile.email)):
            return Verdict("chooser")
        if frames[0].get("emailInput") or re.search(r"choose an account|sign in", text, re.I):
            return Verdict("stuck:google_signed_out", _bare(url))
        return Verdict("unknown", _bare(url))
    if host in ("claude.ai", "claude.com", "www.claude.com"):
        if "/oauth/authorize" in path:
            try:
                ready = bool(page.evaluate(_AUTHORIZE))
            except (CdpError, WebSocketException, TimeoutError):
                ready = False
            return Verdict("consent") if ready else Verdict("loading", _bare(url))
        if path.startswith("/login") or frames[0].get("emailInput"):
            if profile.login == "google" and _visible(page, _GOOGLE_BUTTON):
                return Verdict("google_button")
            if profile.login == "email":
                return Verdict("stuck:email_login", _bare(url))
            return Verdict("stuck:signed_out", _bare(url))
    if url.startswith(("about:", "chrome:")) or not text.strip():
        return Verdict("loading", _bare(url))
    return Verdict("unknown", _bare(url))


def _click_point(page: CdpPage, expression: str) -> bool:
    script = (
        f"(() => {{ const e = {expression}; if (!e) return null; e.scrollIntoView({{block: 'center'}});"
        " const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()"
    )
    try:
        point = page.evaluate(script)
        if not isinstance(point, list) or len(point) != 2:
            return False
        x, y = float(point[0]), float(point[1])
        page.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, session=page.session)
        for kind in ("mousePressed", "mouseReleased"):
            page.call(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
                session=page.session,
            )
        return True
    except (CdpError, WebSocketException, TimeoutError, TypeError, ValueError):
        return False


def click(page: CdpPage, profile: BrowserProfile, kind: str) -> Verdict | None:
    stuck = stuck_in(_frames(page))
    if stuck:
        return stuck
    target = {
        "chooser": _tile(profile.email),
        "google_button": _GOOGLE_BUTTON,
        "consent": "[...document.querySelectorAll('button')].find(e => e.innerText.trim() === 'Authorize' && !e.disabled)",
    }[kind]
    if not _click_point(page, target):
        return Verdict("unknown", f"{kind} button moved before the click")
    return None
