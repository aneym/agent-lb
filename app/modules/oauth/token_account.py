from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    OpenAIAuthClaims,
    clean_account_identity_part,
    extract_id_token_claims,
    generate_unique_account_id,
    normalize_seat_type,
)
from app.core.clients.oauth import OAuthError, OAuthTokens
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.providers import OPENAI_PROVIDER_NAME, get_provider
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.auth_tokens import expiry_datetime


def account_from_tokens(tokens: OAuthTokens, provider_name: str, encryptor: TokenEncryptor) -> Account:
    provider = get_provider(provider_name)
    metadata = provider.account_metadata_from_id_token(tokens.id_token)
    metadata_plan_type = metadata.plan_type if tokens.id_token else None
    default_plan = tokens.plan_type or metadata_plan_type or DEFAULT_PLAN
    plan_type = coerce_account_plan_type(tokens.plan_type or metadata_plan_type, default_plan)
    id_token_encrypted = encryptor.encrypt(tokens.id_token) if tokens.id_token else None
    if provider.requires_id_token and id_token_encrypted is None:
        raise OAuthError("invalid_response", "OAuth response missing id token")
    workspace_id: str | None = None
    workspace_label: str | None = None
    seat_type: str | None = None
    if provider.name == OPENAI_PROVIDER_NAME and tokens.id_token:
        claims = extract_id_token_claims(tokens.id_token)
        auth_claims = claims.auth or OpenAIAuthClaims()
        raw_account_id = auth_claims.chatgpt_account_id or claims.chatgpt_account_id or metadata.account_id
        email = claims.email or metadata.email or tokens.email or DEFAULT_EMAIL
        workspace_id = clean_account_identity_part(auth_claims.workspace_id or claims.workspace_id)
        workspace_label = clean_account_identity_part(auth_claims.workspace_label or claims.workspace_label)
        seat_type = normalize_seat_type(auth_claims.seat_type or claims.seat_type)
        plan_type = coerce_account_plan_type(
            auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type or metadata_plan_type or tokens.plan_type,
            default_plan,
        )
    else:
        raw_account_id = metadata.account_id or tokens.account_id
        email = metadata.email or tokens.email or DEFAULT_EMAIL
    account_id = generate_unique_account_id(raw_account_id, email, workspace_id)

    account = Account(
        id=account_id,
        provider=provider.name,
        chatgpt_account_id=raw_account_id,
        email=email,
        workspace_id=workspace_id,
        workspace_label=workspace_label,
        seat_type=seat_type,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt(tokens.access_token),
        refresh_token_encrypted=encryptor.encrypt(tokens.refresh_token),
        id_token_encrypted=id_token_encrypted,
        last_refresh=utcnow(),
        access_expires_at=expiry_datetime(tokens.access_token, tokens.expires_in),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    return account
