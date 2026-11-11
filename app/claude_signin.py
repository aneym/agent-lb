from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.clients.oauth import OAuthTokens

DEFAULT_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
DEFAULT_PROFILE_PATH = Path.home() / ".claude.json"
WRAPPER_KEY = "claudeAiOauth"
INFERENCE_SCOPE = "user:inference"


class ClaudeSignInError(ValueError):
    pass


@dataclass(frozen=True)
class ClaudeSignIn:
    tokens: OAuthTokens
    scopes: tuple[str, ...]
    expires_at_ms: int | None


def _text(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _oauth_block(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ClaudeSignInError("the Claude sign-in is not a JSON object")
    block = document.get(WRAPPER_KEY, document)
    if not isinstance(block, dict):
        raise ClaudeSignInError(f"{WRAPPER_KEY} is not an object")
    return block


def _profile_identity(profile: dict[str, Any] | None) -> tuple[str | None, str | None]:
    account = (profile or {}).get("oauthAccount")
    if not isinstance(account, dict):
        return None, None
    return _text(account, "accountUuid", "organizationUuid"), _text(account, "emailAddress", "email")


def _expires_in(expires_at_ms: int | None) -> int | None:
    if expires_at_ms is None:
        return None
    return max(0, int(expires_at_ms / 1000 - time.time()))


def parse(raw: str, profile: dict[str, Any] | None = None) -> ClaudeSignIn:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeSignInError(f"the Claude sign-in is not valid JSON (line {exc.lineno})") from None
    block = _oauth_block(document)
    access = _text(block, "accessToken", "access_token")
    refresh = _text(block, "refreshToken", "refresh_token")
    if not access or not refresh:
        raise ClaudeSignInError("the Claude sign-in needs both an access token and a refresh token")
    raw_expiry = block.get("expiresAt", block.get("expires_at"))
    expires_at_ms = int(raw_expiry) if isinstance(raw_expiry, (int, float)) else None
    scopes_raw = block.get("scopes")
    scopes = tuple(str(scope) for scope in scopes_raw) if isinstance(scopes_raw, list) else ()
    if scopes and INFERENCE_SCOPE not in scopes:
        raise ClaudeSignInError(f"the Claude sign-in lacks the {INFERENCE_SCOPE} scope")
    account_id, email = _profile_identity(profile)
    if account_id is None:
        account_id = f"anthropic_{hashlib.sha256(refresh.encode('utf-8')).hexdigest()[:16]}"
    tokens = OAuthTokens(
        access_token=access,
        refresh_token=refresh,
        account_id=account_id,
        email=email,
        plan_type=_text(block, "subscriptionType", "subscription_type"),
        expires_in=_expires_in(expires_at_ms),
    )
    return ClaudeSignIn(tokens=tokens, scopes=scopes, expires_at_ms=expires_at_ms)


def read_profile(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None
