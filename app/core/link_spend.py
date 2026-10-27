from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


class LinkError(Exception):
    pass


def _link(*args: str) -> object:
    binary = os.getenv("AGENT_LB_LINK_CLI") or shutil.which("link-cli")
    if not binary:
        raise LinkError("link-cli is not installed")
    try:
        result = subprocess.run([binary, *args, "--format", "json"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise LinkError("link-cli did not answer") from None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise LinkError(f"link-cli exited {result.returncode} without JSON") from None
    if isinstance(payload, dict) and payload.get("code"):
        raise LinkError(f"link-cli {payload['code']}: {str(payload.get('message', ''))[:200]}")
    return payload[0] if isinstance(payload, list) and payload else payload


def create_card_request(email: str, plan: str, amount_usd: float, renews_on: str) -> dict:
    cents = round(amount_usd * 100)
    context = (
        f"Card for the {plan} renewal of {email} on {renews_on}, routed through Agent LB. "
        f"Agent LB puts this Link card on the claude.ai billing page so the renewal of USD {amount_usd:.2f} "
        "charges it; nothing else is bought."
    )
    payload = _link(
        "spend-request",
        "create",
        "--credential-type",
        "card",
        "--merchant-name",
        "Anthropic",
        "--merchant-url",
        "https://claude.ai",
        "--amount",
        str(cents),
        "--currency",
        "usd",
        "--context",
        context,
        "--line-item",
        f"name:{plan} renewal,unit_amount:{cents},quantity:1",
        "--total",
        f"type:total,display_text:Renewal {renews_on},amount:{cents}",
        "--metadata",
        f"account:{email}",
        "--request-approval=false",
    )
    if not isinstance(payload, dict) or not payload.get("id"):
        raise LinkError("link-cli returned no spend request")
    _link("spend-request", "request-approval", payload["id"], "--filter-output", "id")
    return {"id": payload["id"], **request_state(payload["id"])}


def request_state(request_id: str) -> dict:
    payload = _link("spend-request", "retrieve", request_id, "--filter-output", "status")
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
        raise LinkError("link-cli returned no status")
    return {"status": payload["status"], "approvalUrl": f"https://app.link.com/activity/approve/{request_id}"}


def load_card(request_id: str, directory: Path) -> dict:
    path = directory / "card.json"
    try:
        _link(
            "spend-request",
            "retrieve",
            request_id,
            "--include",
            "card",
            "--output-file",
            str(path),
            "--filter-output",
            "status",
        )
        return json.loads(path.read_text())["card"]
    except (OSError, ValueError, KeyError):
        raise LinkError("Link did not return a card") from None
    finally:
        path.unlink(missing_ok=True)
