from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import reset_cli

pytestmark = pytest.mark.unit


def _args(*, command="redeem", yes=True, credit_id=None, override_daily_limit=False):
    return SimpleNamespace(
        base_url="http://127.0.0.1:2455",
        timeout=3.0,
        account="alex@example.test",
        resets_command=command,
        yes=yes,
        credit_id=credit_id,
        override_daily_limit=override_daily_limit,
    )


def _account(account_id="acc-1"):
    return {"accountId": account_id, "email": "alex@example.test"}


def _inventory(**overrides):
    result = {"availableCount": 1, "credits": [{"id": "credit-1", "status": "available"}]}
    result.update(overrides)
    return result


def test_duplicate_exact_email_does_not_fetch_inventory_or_redeem(monkeypatch):
    calls = []

    def request(_base, path, _timeout, *, body=None):
        calls.append((path, body))
        return {"accounts": [_account("acc-1"), _account("acc-2")]}

    monkeypatch.setattr(reset_cli, "_request", request)
    with pytest.raises(SystemExit) as exc:
        reset_cli.run(_args())
    assert exc.value.code == 2
    assert calls == [("/api/accounts", None)]


def test_noninteractive_redemption_needs_yes_and_never_posts(monkeypatch):
    calls = []

    def request(_base, path, _timeout, *, body=None):
        calls.append((path, body))
        return {"accounts": [_account()]} if path == "/api/accounts" else _inventory()

    monkeypatch.setattr(reset_cli, "_request", request)
    monkeypatch.setattr(reset_cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(SystemExit) as exc:
        reset_cli.run(_args(yes=False))
    assert exc.value.code == 2
    assert len(calls) == 2


def test_known_ineligible_credit_is_banked_but_not_redeemed(monkeypatch, capsys):
    calls = []

    def request(_base, path, _timeout, *, body=None):
        calls.append(path)
        return {"accounts": [_account()]} if path == "/api/accounts" else _inventory(
            redeemableNow=False, ineligibleReason="provider_not_eligible"
        )

    monkeypatch.setattr(reset_cli, "_request", request)
    with pytest.raises(SystemExit) as exc:
        reset_cli.run(_args())
    assert exc.value.code == 2
    assert len(calls) == 2
    assert "Currently redeemable: no (provider_not_eligible)" in capsys.readouterr().out


def test_success_posts_once_without_override(monkeypatch, capsys):
    calls = []

    def request(_base, path, _timeout, *, body=None):
        calls.append((path, body))
        if path == "/api/accounts":
            return {"accounts": [_account()]}
        if body is None:
            return _inventory(redeemableNow=True)
        return {"status": "redeemed", "code": "reset", "windowsReset": 2}

    monkeypatch.setattr(reset_cli, "_request", request)
    reset_cli.run(_args(credit_id="credit-1"))
    assert calls[-1] == ("/api/accounts/acc-1/rate-limit-reset-credits/consume", {"creditId": "credit-1"})
    assert len(calls) == 3
    assert "Redemption: redeemed (code: reset; windows reset: 2)" in capsys.readouterr().out


def test_noop_exits_nonzero_without_retry(monkeypatch, capsys):
    calls = []

    def request(_base, path, _timeout, *, body=None):
        calls.append(path)
        if path == "/api/accounts":
            return {"accounts": [_account()]}
        if body is None:
            return _inventory()
        return {"status": "not_redeemed", "code": "not_eligible", "windowsReset": 0}

    monkeypatch.setattr(reset_cli, "_request", request)
    with pytest.raises(SystemExit) as exc:
        reset_cli.run(_args())
    assert exc.value.code == 1
    assert len(calls) == 3
    assert "code: not_eligible" in capsys.readouterr().out


def test_explicit_claude_daily_override_is_sent_once(monkeypatch):
    calls = []

    def request(_base, path, _timeout, *, body=None):
        calls.append((path, body))
        if path == "/api/accounts":
            return {"accounts": [{**_account(), "provider": "anthropic"}]}
        if body is None:
            return _inventory(redeemableNow=True)
        return {"status": "redeemed", "code": "reset", "windowsReset": 2}

    monkeypatch.setattr(reset_cli, "_request", request)
    reset_cli.run(_args(override_daily_limit=True))
    assert calls[-1][1] == {"overrideDailyLimit": True}
    assert len(calls) == 3
