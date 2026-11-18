from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping

_MATCHER_PATH = Path(__file__).resolve().parents[2] / "config/coding-agents/hooks/claim_codes.py"
_TOOL_RESULT_TYPES = frozenset({"mcp_call", "mcp_tool_call_output"})


def _load_matcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_lb_claim_codes", _MATCHER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"claim-code matcher missing at {_MATCHER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MATCHER = _load_matcher()


def _is_tool_result(item_type: JsonValue) -> bool:
    return isinstance(item_type, str) and (item_type.endswith("_call_output") or item_type in _TOOL_RESULT_TYPES)


def mask_tool_result(item: JsonValue) -> JsonValue:
    if not is_json_mapping(item) or not _is_tool_result(item.get("type")):
        return item
    output = item.get("output")
    masked = _MATCHER.scrub(output)
    if masked == output:
        return item
    return {**item, "output": masked}
