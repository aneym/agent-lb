from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CURSOR_PROVIDER_NAME = "cursor"
GROK_PROVIDER_NAME = "grok"
CURSOR_CONSENT_ACTION = "agent login"
CURSOR_CONSENT_SOURCE = "https://cursor.com/docs/cli/reference/authentication"

OnboardingState = Literal[
    "discovered",
    "configured",
    "authenticated",
    "catalog_verified",
    "entitled",
    "conformance_passed",
    "live_verified",
    "enabled",
    "stale",
    "blocked",
    "disabled",
]
Disposition = Literal["enabled", "temporarily_unavailable", "disabled", "blocked"]
MeterMode = Literal["measured", "provider_managed"]
Headroom = Literal["KNOWN", "NOT_APPLICABLE", "UNKNOWN", "STALE", "EXHAUSTED", "NOT_EXPOSED"]
Admission = Literal["NATIVE_MANAGED_ELIGIBLE", "NATIVE_MANAGED_BLOCKED"]
Entitlement = Literal["unknown", "verified"]
InclusionOverage = Literal["unknown", "verified"]
Proof = Literal["unverified", "verified"]


@dataclass(frozen=True, slots=True)
class ConsentPacket:
    provider: str
    action: str
    source: str


@dataclass(frozen=True, slots=True)
class CatalogObservation:
    status: Literal["unverified"]
    models: tuple[str, ...]
    discovery: str
    invoked: Literal[False] = False
    observed_at: None = None


@dataclass(frozen=True, slots=True)
class UsageObservation:
    meter_mode: MeterMode
    headroom: Headroom
    admission: Admission
    entitlement: Entitlement
    inclusion_overage: InclusionOverage
    reconciliation: str
    invoked: Literal[False] = False
    observed_at: None = None


@dataclass(frozen=True, slots=True)
class AdapterDeclaration:
    authentication_mode: str
    catalog_discovery: str
    native_cc_transport: Literal["unverified"]
    backup_runtime: str
    entitlement_source: str
    meters: str
    freshness: Literal["no_observation"]
    account_model_mapping: tuple[str, ...]
    limits: Literal["unverified"]
    refresh: Literal["not_implemented"]
    errors: Literal["unclassified"]
    cancellation: Literal["not_wired"]
    usage_reconciliation: str
    supported_versions: Literal["unpinned"]


@dataclass(frozen=True, slots=True)
class NativeBackupCandidate:
    provider: str
    enabled: bool
    disposition: Disposition
    onboarding_state: OnboardingState
    observed_states: tuple[OnboardingState, ...]
    execution_mode: Literal["provider_native"] | None
    missing_proof: str
    consent: ConsentPacket | None
    catalog: CatalogObservation
    usage: UsageObservation
    declaration: AdapterDeclaration
    backup_reason: str | None = None
    account_reference: None = None
    plan_admission: Proof = "unverified"
    concurrency_cap: Proof = "unverified"
    quota_refusal: Proof = "unverified"
    usage_receipts: Proof = "unverified"
    copies_browser_session: Literal[False] = False
    holds_credentials: Literal[False] = False
    spends_api_credit: Literal[False] = False
    joins_live_provider_registry: Literal[False] = False

    def dispatch_admitted(self) -> bool:
        if self.enabled is not True:
            return False
        if self.disposition != "enabled" or self.onboarding_state != "enabled":
            return False
        if self.execution_mode != "provider_native":
            return False
        if self.usage.entitlement != "verified" or self.usage.inclusion_overage != "verified":
            return False
        if self.usage.meter_mode != "provider_managed":
            return False
        if self.usage.headroom != "NOT_EXPOSED":
            return False
        if self.usage.admission != "NATIVE_MANAGED_ELIGIBLE":
            return False
        return (
            self.plan_admission == "verified"
            and self.concurrency_cap == "verified"
            and self.quota_refusal == "verified"
            and self.usage_receipts == "verified"
        )


def _unobserved_catalog(discovery: str) -> CatalogObservation:
    return CatalogObservation(status="unverified", models=(), discovery=discovery)


def _unobserved_usage(reconciliation: str) -> UsageObservation:
    return UsageObservation(
        meter_mode="provider_managed",
        headroom="NOT_EXPOSED",
        admission="NATIVE_MANAGED_BLOCKED",
        entitlement="unknown",
        inclusion_overage="unknown",
        reconciliation=reconciliation,
    )


def _declaration(
    *,
    authentication_mode: str,
    catalog_discovery: str,
    backup_runtime: str,
    usage_reconciliation: str,
) -> AdapterDeclaration:
    return AdapterDeclaration(
        authentication_mode=authentication_mode,
        catalog_discovery=catalog_discovery,
        native_cc_transport="unverified",
        backup_runtime=backup_runtime,
        entitlement_source="unknown",
        meters="provider_managed",
        freshness="no_observation",
        account_model_mapping=(),
        limits="unverified",
        refresh="not_implemented",
        errors="unclassified",
        cancellation="not_wired",
        usage_reconciliation=usage_reconciliation,
        supported_versions="unpinned",
    )


_CURSOR = NativeBackupCandidate(
    provider=CURSOR_PROVIDER_NAME,
    enabled=False,
    disposition="blocked",
    onboarding_state="blocked",
    observed_states=("discovered", "blocked"),
    execution_mode="provider_native",
    missing_proof=(
        "Official Cursor login cannot be completed headlessly. "
        f"Run `{CURSOR_CONSENT_ACTION}` and complete the browser consent it opens."
    ),
    consent=ConsentPacket(
        provider=CURSOR_PROVIDER_NAME,
        action=CURSOR_CONSENT_ACTION,
        source=CURSOR_CONSENT_SOURCE,
    ),
    catalog=_unobserved_catalog("Cursor.models.list"),
    usage=_unobserved_usage("Agent.get_usage"),
    declaration=_declaration(
        authentication_mode="official_cli_browser_login",
        catalog_discovery="Cursor.models.list",
        backup_runtime="cursor-sdk Agent",
        usage_reconciliation="Agent.get_usage",
    ),
    backup_reason=(
        "Native Claude Code model transport for Cursor is unverified. "
        "The official SDK is an agent SDK, not a raw subscription model API."
    ),
)

_GROK = NativeBackupCandidate(
    provider=GROK_PROVIDER_NAME,
    enabled=False,
    disposition="disabled",
    onboarding_state="disabled",
    observed_states=("disabled",),
    execution_mode=None,
    missing_proof=(
        "Disabled: the owner does not believe a Grok subscription exists. "
        "xAI API credit is not spent, and API-key billing is not enabled."
    ),
    consent=None,
    catalog=_unobserved_catalog("not_called"),
    usage=_unobserved_usage("not_called"),
    declaration=_declaration(
        authentication_mode="not_configured",
        catalog_discovery="not_called",
        backup_runtime="not_enabled",
        usage_reconciliation="not_called",
    ),
)


def cursor_native_backup() -> NativeBackupCandidate:
    return _CURSOR


def grok_native_backup() -> NativeBackupCandidate:
    return _GROK


def list_native_backup_candidates() -> tuple[NativeBackupCandidate, ...]:
    return (_CURSOR, _GROK)
