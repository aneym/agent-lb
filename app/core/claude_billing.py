from __future__ import annotations

import json
import time

from app.core.cdp_page import CdpError, CdpPage, page_targets

BILLING_URL = "https://claude.ai/settings/billing"

SIGNED_IN = """(async () => {
  try { const r = await fetch('/api/account'); if (!r.ok) return null; return (await r.json()).email_address || null; }
  catch (e) { return null; }
})()"""

SIGNED_IN_OR_LOGIN = "location.pathname.startsWith('/login') ? '-' : " + SIGNED_IN

_SETTINGS = "[...document.querySelectorAll('[role=dialog]')].find(d => /^\\s*Settings/.test(d.innerText))"

_FORM = "[...document.querySelectorAll('[role=dialog]')].find(d => /^\\s*Add payment method/.test(d.innerText))"

_OPEN_PAYMENT_FORM = f"""(() => {{
  const settings = {_SETTINGS};
  if (!settings || /Loading/.test(settings.innerText)) return false;
  const button = [...settings.querySelectorAll('button')].find(e => e.innerText.trim() === 'Add payment method');
  if (!button) return false;
  button.click();
  return true;
}})()"""

_FORM_OPEN = f"Boolean({_FORM})"

_FIND_CARD = """(() => {
  const find = w => {
    try { if (w.document.querySelector('input[name=number]')) return w; } catch (e) { return null; }
    for (let i = 0; i < w.frames.length; i++) { const hit = find(w.frames[i]); if (hit) return hit; }
    return null;
  };
  const hit = find(window);
  if (hit) window.__agentLbCard = hit;
  return Boolean(hit);
})()"""

_MAKE_DEFAULT = f"""(() => {{
  const form = {_FORM};
  if (!form) return false;
  const box = [...form.querySelectorAll('[role=checkbox]')].find(e => /set as default/i.test(e.innerText || ''));
  if (!box) return false;
  if (box.getAttribute('aria-checked') !== 'true') box.click();
  return box.getAttribute('aria-checked') === 'true';
}})()"""

_SAVE = f"""(() => {{
  const form = {_FORM};
  const button = form && [...form.querySelectorAll('button')].find(e => !e.disabled && e.innerText.trim() === 'Add');
  if (!button) return false;
  button.click();
  return true;
}})()"""

_ERRORS = rf"""(() => {{
  const form = {_FORM};
  if (!form) return null;
  const alert = form.querySelector('[role=alert]');
  if (alert && alert.innerText.trim()) return alert.innerText.trim().slice(0, 200);
  const line = form.innerText.split('\n').find(
    l => !/payment method is charged/i.test(l) && /declin|insufficient|invalid|incorrect|could not|failed|try a different|went wrong/i.test(l)
  );
  return line ? line.trim().slice(0, 200) : null;
}})()"""


def _listed(last4: str) -> str:
    return (
        f"(() => {{ const settings = {_SETTINGS}; if (!settings) return -1;"
        f" return settings.innerText.split({json.dumps('•••• ' + last4)}).length - 1; }})()"
    )


class BillingError(Exception):
    pass


def claude_tab(port: int) -> dict | None:
    return next((tab for tab in page_targets(port) if str(tab.get("url", "")).startswith("https://claude.ai/")), None)


def signed_in_email(port: int) -> str | None:
    tab = claude_tab(port)
    page = CdpPage(port, target_id=tab["id"]) if tab else CdpPage(port, BILLING_URL, background=True)
    try:
        page.wait_for("document.readyState === 'complete'", seconds=20)
        signed = page.wait_for(SIGNED_IN_OR_LOGIN, seconds=10)
        return signed if signed and signed != "-" else None
    finally:
        page.close()


def _focus(name: str) -> str:
    return (
        "(() => { const el = window.__agentLbCard && window.__agentLbCard.document.querySelector("
        + json.dumps(f"input[name={name}]")
        + "); if (!el) return false; el.focus(); el.select && el.select(); return true; })()"
    )


def _billing_page(port: int) -> CdpPage:
    tab = claude_tab(port)
    if not tab:
        return CdpPage(port, BILLING_URL, background=True)
    page = CdpPage(port, target_id=tab["id"])
    page.navigate(BILLING_URL)
    return page


def put_card(port: int, email: str, card: dict, *, save: bool = True) -> str:
    last4 = card["number"][-4:]
    page = _billing_page(port)
    try:
        page.wait_for("document.readyState === 'complete'", seconds=20)
        if page.wait_for(SIGNED_IN, seconds=10) != email:
            raise BillingError(f"browser profile is not signed in to claude.ai as {email}")
        if not page.wait_for(_OPEN_PAYMENT_FORM, seconds=30):
            raise BillingError("no Add payment method button in the billing settings")
        if not page.wait_for(_FORM_OPEN, seconds=15):
            raise BillingError("Add payment method form did not open")
        before = page.evaluate(_listed(last4)) or 0
        frame = None
        deadline = time.monotonic() + 20
        while frame is None and time.monotonic() < deadline:
            for session in list(page.frame_sessions):
                try:
                    if page.evaluate(_FIND_CARD, session=session):
                        frame = session
                        break
                except CdpError:
                    continue
            if frame is None:
                time.sleep(0.5)
                page.call("Runtime.evaluate", {"expression": "1"}, session=page.session)
        if frame is None:
            raise BillingError("card form did not appear")
        address = card.get("billing_address") or {}
        values = [
            ("number", card["number"]),
            ("expiry", f"{int(card['exp_month']):02d}{int(card['exp_year']) % 100:02d}"),
            ("cvc", card["cvc"]),
            ("postalCode", address.get("postal_code")),
        ]
        for name, value in values:
            if value and page.evaluate(_focus(name), session=frame):
                page.insert_text(str(value), session=frame)
        if not save:
            return "unsaved"
        page.evaluate(_MAKE_DEFAULT)
        if not page.wait_for(_SAVE, seconds=10):
            raise BillingError(page.evaluate(_ERRORS) or "the Add button never enabled")
        confirmed = page.wait_for(f"!{_FORM_OPEN} && {_listed(last4)} > {int(before)}", seconds=60)
        if not confirmed:
            raise BillingError(page.evaluate(_ERRORS) or "billing settings never listed the new card")
        return last4
    finally:
        page.close()
