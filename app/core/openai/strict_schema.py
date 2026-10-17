from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping


@dataclass(frozen=True)
class StrictSchemaError:
    code: str
    message: str
    param: str


def validate_strict_json_schema(
    schema: JsonValue,
    *,
    name: str | None,
    param: str,
) -> StrictSchemaError | None:

    label = name or "response_format"
    violation = _find_violation(schema, ())
    if violation is None:
        return None
    context_path, reason = violation
    rendered_context = _render_context(context_path)
    message = f"Invalid schema for response_format '{label}': In context={rendered_context}, {reason}."
    return StrictSchemaError(
        code="invalid_json_schema",
        message=message,
        param=param,
    )


def validate_strict_function_tool_schema(
    schema: JsonValue,
    *,
    name: str | None,
    param: str,
) -> StrictSchemaError | None:

    label = name or "function"
    violation = _find_violation(schema, ())
    if violation is None:
        return None
    context_path, reason = violation
    rendered_context = _render_context(context_path)
    message = f"Invalid schema for function '{label}': In context={rendered_context}, {reason}."
    return StrictSchemaError(
        code="invalid_function_parameters",
        message=message,
        param=param,
    )


def _render_context(path: tuple[str, ...]) -> str:
    if not path:
        return "()"
    parts = ", ".join(f"'{part}'" for part in path)
    return f"({parts})"


def _find_violation(
    node: JsonValue,
    path: tuple[str, ...],
) -> tuple[tuple[str, ...], str] | None:
    if not is_json_mapping(node):
        return None

    if "type" not in node and not _has_combinator(node) and not _is_ref(node):
        return path, "schema must have a 'type' key"

    schema_type = node.get("type")

    if schema_type == "object" or "properties" in node:
        additional = node.get("additionalProperties")
        if additional is not False:
            return (
                path,
                "'additionalProperties' is required to be supplied and to be false",
            )

        properties = node.get("properties")
        if is_json_mapping(properties):
            required_raw = node.get("required")
            required_set: set[str] = set()
            if is_json_list(required_raw):
                required_set = {item for item in required_raw if isinstance(item, str)}
            missing_required = [str(prop_name) for prop_name in properties.keys() if str(prop_name) not in required_set]
            if missing_required:
                missing_list = ", ".join(f"'{name}'" for name in missing_required)
                return (
                    path,
                    "'required' is required to be supplied and to be an array including every key in properties. "
                    f"Missing {missing_list}",
                )
            for prop_name, prop_schema in properties.items():
                violation = _find_violation(
                    prop_schema,
                    path + ("properties", str(prop_name)),
                )
                if violation is not None:
                    return violation

    if schema_type == "array" or "items" in node:
        items = node.get("items")
        if items is not None:
            violation = _find_violation(items, path + ("items",))
            if violation is not None:
                return violation

    for combinator in ("anyOf", "oneOf", "allOf"):
        candidates = node.get(combinator)
        if is_json_list(candidates):
            for index, candidate in enumerate(candidates):
                violation = _find_violation(
                    candidate,
                    path + (combinator, str(index)),
                )
                if violation is not None:
                    return violation

    defs = node.get("$defs") or node.get("definitions")
    if is_json_mapping(defs):
        for def_name, def_schema in defs.items():
            violation = _find_violation(
                def_schema,
                path + ("$defs", str(def_name)),
            )
            if violation is not None:
                return violation

    return None


def _has_combinator(node: Mapping[str, JsonValue]) -> bool:
    return any(is_json_list(node.get(name)) for name in ("anyOf", "oneOf", "allOf"))


def _is_ref(node: Mapping[str, JsonValue]) -> bool:
    return isinstance(node.get("$ref"), str)
