from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.core.providers import (
    ANTHROPIC_PROVIDER_NAME,
    GLM_PROVIDER_NAME,
    KIMI_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    ProviderLookupError,
    get_provider,
    list_provider_names,
)
from app.core.providers.native_backup import (
    CURSOR_CONSENT_ACTION,
    CURSOR_PROVIDER_NAME,
    GROK_PROVIDER_NAME,
    cursor_native_backup,
    grok_native_backup,
    list_native_backup_candidates,
)
from app.core.providers.openrouter import OPENROUTER_PROVIDER_NAME

pytestmark = pytest.mark.unit

_SECRET_MARKERS = (
    "crsr_",
    "sk-",
    "xai-",
    "Bearer ",
    "CURSOR_API_KEY",
    "XAI_API_KEY",
    "os.environ",
    "subprocess",
    "aiohttp",
    "httpx",
    "urllib",
    "webbrowser",
    "auth.json",
)


def test_import_does_not_join_the_live_provider_registry() -> None:
    before = list_provider_names()

    # 5b88b132 added Kimi; 55ba9871 added OpenRouter to the live provider registry.
    assert before == (
        ANTHROPIC_PROVIDER_NAME,
        GLM_PROVIDER_NAME,
        KIMI_PROVIDER_NAME,
        OPENAI_PROVIDER_NAME,
        OPENROUTER_PROVIDER_NAME,
    )
    assert CURSOR_PROVIDER_NAME not in before
    assert GROK_PROVIDER_NAME not in before
    for name in (CURSOR_PROVIDER_NAME, GROK_PROVIDER_NAME):
        with pytest.raises(ProviderLookupError):
            get_provider(name)


def test_cursor_is_discovered_then_blocked_without_inferred_entitlement() -> None:
    cursor = cursor_native_backup()

    assert cursor.provider == CURSOR_PROVIDER_NAME
    assert cursor.enabled is False
    assert cursor.disposition == "blocked"
    assert cursor.onboarding_state == "blocked"
    assert cursor.observed_states == ("discovered", "blocked")
    assert cursor.execution_mode == "provider_native"
    assert cursor.consent is not None
    assert cursor.consent.action == CURSOR_CONSENT_ACTION
    assert cursor.catalog.status == "unverified"
    assert cursor.catalog.models == ()
    assert cursor.catalog.invoked is False
    assert cursor.catalog.observed_at is None
    assert cursor.usage.meter_mode == "provider_managed"
    assert cursor.usage.headroom == "NOT_EXPOSED"
    assert cursor.usage.admission == "NATIVE_MANAGED_BLOCKED"
    assert cursor.usage.entitlement == "unknown"
    assert cursor.usage.inclusion_overage == "unknown"
    assert cursor.usage.invoked is False
    assert cursor.declaration.native_cc_transport == "unverified"
    assert cursor.declaration.freshness == "no_observation"
    assert cursor.declaration.account_model_mapping == ()
    assert cursor.copies_browser_session is False
    assert cursor.holds_credentials is False
    assert cursor.spends_api_credit is False
    assert cursor.joins_live_provider_registry is False
    assert cursor.account_reference is None
    assert cursor.dispatch_admitted() is False


def test_grok_stays_disabled_without_api_spend() -> None:
    grok = grok_native_backup()

    assert grok.provider == GROK_PROVIDER_NAME
    assert grok.enabled is False
    assert grok.disposition == "disabled"
    assert grok.onboarding_state == "disabled"
    assert grok.execution_mode is None
    assert grok.consent is None
    assert grok.catalog.invoked is False
    assert grok.catalog.models == ()
    assert grok.usage.entitlement == "unknown"
    assert grok.usage.admission == "NATIVE_MANAGED_BLOCKED"
    assert grok.spends_api_credit is False
    assert grok.holds_credentials is False
    assert grok.dispatch_admitted() is False


def test_both_records_are_kept_and_neither_is_enabled() -> None:
    candidates = list_native_backup_candidates()

    assert tuple(candidate.provider for candidate in candidates) == (CURSOR_PROVIDER_NAME, GROK_PROVIDER_NAME)
    assert all(candidate.enabled is False for candidate in candidates)
    assert all(candidate.disposition != "enabled" for candidate in candidates)


def test_unknown_entitlement_blocks_even_if_the_label_is_flipped() -> None:
    cursor = cursor_native_backup()
    relabeled = replace(
        cursor,
        enabled=True,
        disposition="enabled",
        onboarding_state="enabled",
        usage=replace(cursor.usage, admission="NATIVE_MANAGED_ELIGIBLE"),
    )

    assert relabeled.dispatch_admitted() is False
    assert relabeled.joins_live_provider_registry is False


def test_provider_managed_dispatch_needs_verified_inclusion_and_still_stays_off_the_live_registry() -> None:
    cursor = cursor_native_backup()
    proved = replace(
        cursor,
        enabled=True,
        disposition="enabled",
        onboarding_state="enabled",
        usage=replace(
            cursor.usage,
            admission="NATIVE_MANAGED_ELIGIBLE",
            entitlement="verified",
            inclusion_overage="verified",
        ),
    )

    assert proved.dispatch_admitted() is False
    fully_proved = replace(
        proved,
        plan_admission="verified",
        concurrency_cap="verified",
        quota_refusal="verified",
        usage_receipts="verified",
    )
    assert fully_proved.dispatch_admitted() is True
    assert fully_proved.joins_live_provider_registry is False
    assert cursor_native_backup().dispatch_admitted() is False
    with pytest.raises(ProviderLookupError):
        get_provider(CURSOR_PROVIDER_NAME)


def test_native_backup_source_has_no_secret_or_network_call() -> None:
    source = Path(cursor_native_backup.__module__.replace(".", "/") + ".py")
    module_path = Path(__file__).resolve().parents[2] / source
    text = module_path.read_text()

    for marker in _SECRET_MARKERS:
        assert marker not in text
    assert CURSOR_CONSENT_ACTION in text
