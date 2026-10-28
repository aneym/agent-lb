from __future__ import annotations

import re
from typing import Any

BILLING_HEADER_PREFIX = "x-anthropic-billing-header:"

_UA_ENTRYPOINT = re.compile(r"^(claude-cli/\S+ \(external, )(sdk-[\w.-]+)")
_BILLING_ENTRYPOINT = re.compile(r"(cc_entrypoint=)(sdk-[\w.-]+)")


def relabel_user_agent(user_agent: str, label: str) -> str:
    if not label:
        return user_agent
    return _UA_ENTRYPOINT.sub(lambda m: m.group(1) + label, user_agent, count=1)


def relabel_billing_block(body: dict[str, Any], label: str) -> bool:
    system = body.get("system")
    if not label or not isinstance(system, list) or not system:
        return False
    block = system[0]
    if not isinstance(block, dict) or not isinstance(block.get("text"), str):
        return False
    text: str = block["text"]
    if not text.startswith(BILLING_HEADER_PREFIX):
        return False
    relabeled = _BILLING_ENTRYPOINT.sub(lambda m: m.group(1) + label, text, count=1)
    if relabeled == text:
        return False
    block["text"] = relabeled
    return True
