from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _oauth_state_token(authorization_url: str) -> str:
    parsed = urlparse(authorization_url)
    return parse_qs(parsed.query)["state"][0]
