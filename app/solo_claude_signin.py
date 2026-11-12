from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import aiohttp

from app.claude_signin import ClaudeSignIn, ClaudeSignInError
from app.core.anthropic.oauth import build_anthropic_authorization_url, exchange_anthropic_authorization_code
from app.core.clients.oauth import OAuthError, generate_pkce_pair
from app.core.providers import get_provider


@dataclass(frozen=True)
class PendingSignIn:
    url: str
    state: str
    verifier: str


def start() -> PendingSignIn:
    config = get_provider("anthropic").oauth_config()
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    url = build_anthropic_authorization_url(
        state=state,
        code_challenge=challenge,
        authorize_url=config.authorize_url,
        client_id=config.client_id,
        redirect_uri=config.redirect_uri,
        scope=config.scope,
        extra_params=config.authorization_extra_params,
    )
    return PendingSignIn(url=url, state=state, verifier=verifier)


def parse_pasted(pasted: str) -> tuple[str, str | None]:
    text = pasted.strip()
    if not text:
        raise ClaudeSignInError("no code was pasted")
    parsed = urlparse(text)
    if parsed.scheme:
        params = parse_qs(parsed.query)
        params.update(parse_qs(parsed.fragment))
        code = (params.get("code") or [None])[0]
        if not code:
            raise ClaudeSignInError("the pasted address carries no code")
        return code, (params.get("state") or [None])[0]
    code, _, state = text.partition("#")
    return code, state or None


async def finish(pending: PendingSignIn, pasted: str) -> ClaudeSignIn:
    code, state = parse_pasted(pasted)
    if state is not None and state != pending.state:
        raise ClaudeSignInError("the pasted code belongs to a different sign-in; start again")
    config = get_provider("anthropic").oauth_config()
    try:
        async with aiohttp.ClientSession() as session:
            tokens = await exchange_anthropic_authorization_code(
                code=code,
                code_verifier=pending.verifier,
                state=pending.state,
                redirect_uri=config.redirect_uri,
                token_url=config.token_url,
                client_id=config.client_id,
                session=session,
            )
    except OAuthError as exc:
        raise ClaudeSignInError(f"Claude refused the code: {exc.message}") from None
    return ClaudeSignIn(tokens=tokens, scopes=tuple(config.scope.split()), expires_at_ms=None)
