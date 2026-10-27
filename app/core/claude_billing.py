from __future__ import annotations

import json
import time

from app.core.cdp_page import CdpError, CdpPage

BILLING_URL = "https://claude.ai/settings/billing"

_SIGNED_IN = """(async () => {
  try { const r = await fetch('/api/account'); if (!r.ok) return null; return (await r.json()).email_address || null; }
  catch (e) { return null; }
})()"""

_OPEN_PAYMENT_FORM = """(() => {
  const label = e => (e.innerText || e.getAttribute('aria-label') || '').trim();
  const buttons = [...document.querySelectorAll('button, a')];
  const direct = buttons.find(e => /^(update|add|change) payment method$/i.test(label(e)));
  if (direct) { direct.click(); return true; }
  for (const e of buttons) {
    if (!/^(update|change|edit)$/i.test(label(e))) continue;
    let node = e;
    for (let i = 0; i < 5 && node; i++, node = node.parentElement) {
      if (/payment/i.test(node.innerText || '')) { e.click(); return true; }
    }
  }
  return false;
})()"""

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

_SAVE = """(() => {
  const label = e => (e.innerText || e.getAttribute('aria-label') || '').trim();
  const scope = document.querySelector('[role=dialog]') || document;
  const button = [...scope.querySelectorAll('button')].find(
    e => !e.disabled && /^(save|update|confirm|add|save card|update card|add card|update payment method)$/i.test(label(e))
  );
  if (!button) return false;
  button.click();
  return true;
})()"""

_ERRORS = r"""(() => {
  const text = document.body.innerText;
  const line = text.split('\n').find(l => /declin|insufficient|invalid|incorrect|could not|failed|try a different/i.test(l));
  return line ? line.trim().slice(0, 200) : null;
})()"""


class BillingError(Exception):
    pass


def signed_in_email(port: int) -> str | None:
    page = CdpPage(port, BILLING_URL)
    try:
        page.wait_for("document.readyState === 'complete'", seconds=20)
        return page.wait_for(_SIGNED_IN, seconds=10)
    finally:
        page.close()


def _focus(name: str) -> str:
    return (
        "(() => { const el = window.__agentLbCard && window.__agentLbCard.document.querySelector("
        + json.dumps(f"input[name={name}]")
        + "); if (!el) return false; el.focus(); el.select && el.select(); return true; })()"
    )


def put_card(port: int, email: str, card: dict, *, save: bool = True) -> str:
    page = CdpPage(port, BILLING_URL)
    try:
        page.wait_for("document.readyState === 'complete'", seconds=20)
        if page.wait_for(_SIGNED_IN, seconds=10) != email:
            raise BillingError(f"browser profile is not signed in to claude.ai as {email}")
        if not page.wait_for(_OPEN_PAYMENT_FORM, seconds=15):
            raise BillingError("no payment method button on the billing page")
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
        time.sleep(1)
        if not page.evaluate(_SAVE):
            raise BillingError("no save button on the card form")
        last4 = card["number"][-4:]
        confirmed = page.wait_for(
            f"!document.querySelector('[role=dialog]') && document.body.innerText.includes({json.dumps(last4)})",
            seconds=40,
        )
        if not confirmed:
            raise BillingError(page.evaluate(_ERRORS) or "billing page never showed the new card")
        return last4
    finally:
        page.close()
