

from __future__ import annotations

from typing import cast

import pytest

from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.strict_schema import (
    validate_strict_function_tool_schema,
    validate_strict_json_schema,
)
from app.core.types import JsonValue
from app.modules.proxy.request_policy import (
    enforce_strict_function_tools_format,
    enforce_strict_text_format,
    normalize_responses_request_payload,
)


def _json_object(value: object) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], value)


def test_strict_root_missing_additional_properties():
    schema: JsonValue = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    violation = validate_strict_json_schema(schema, name="person", param="text.format.schema")
    assert violation is not None
    assert violation.code == "invalid_json_schema"
    assert violation.param == "text.format.schema"
    assert "context=()" in violation.message
    assert "additionalProperties" in violation.message
    assert "person" in violation.message


def test_strict_root_additional_properties_true_rejected():
    schema: JsonValue = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": True,
    }
    violation = validate_strict_json_schema(schema, name="p", param="text.format.schema")
    assert violation is not None
    assert "context=()" in violation.message


def test_strict_nested_missing_additional_properties():
    schema: JsonValue = {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            }
        },
        "required": ["nodes"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="graph", param="text.format.schema")
    assert violation is not None
    assert "context=('properties', 'nodes', 'items')" in violation.message


def test_strict_dict_str_any_pattern_rejected():


    schema: JsonValue = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "attributes": {"type": "object", "additionalProperties": True},
        },
        "required": ["name", "attributes"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="EntityWithAttrs", param="text.format.schema")
    assert violation is not None
    assert "context=('properties', 'attributes')" in violation.message


def test_strict_empty_schema_node_rejected():


    schema: JsonValue = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "extra": {},
        },
        "required": ["name", "extra"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="withAny", param="text.format.schema")
    assert violation is not None
    assert "context=('properties', 'extra')" in violation.message
    assert "must have a 'type' key" in violation.message


def test_strict_valid_schema_passes():
    schema: JsonValue = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name", "age"],
        "additionalProperties": False,
    }
    assert validate_strict_json_schema(schema, name="person", param="text.format.schema") is None


def test_strict_required_must_list_every_property():


    schema: JsonValue = {
        "type": "object",
        "properties": {"x": {"type": "string"}, "y": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="p", param="text.format.schema")
    assert violation is not None
    assert "required" in violation.message
    assert "'y'" in violation.message
    assert "context=()" in violation.message


def test_strict_required_empty_array_rejected():


    schema: JsonValue = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": [],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="p", param="text.format.schema")
    assert violation is not None
    assert "'x'" in violation.message


def test_strict_required_missing_array_rejected():
    schema: JsonValue = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="p", param="text.format.schema")
    assert violation is not None
    assert "'x'" in violation.message


def test_strict_combinator_recurses_into_branches():
    schema: JsonValue = {
        "type": "object",
        "properties": {
            "value": {
                "anyOf": [
                    {"type": "string"},

                    {
                        "type": "object",
                        "properties": {"x": {"type": "integer"}},
                        "required": ["x"],
                    },
                ]
            }
        },
        "required": ["value"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="union", param="text.format.schema")
    assert violation is not None
    assert "anyOf" in violation.message


def test_normalize_responses_payload_rejects_strict_violation():
    payload = _json_object(
        {
            "model": "gpt-5.5",
            "instructions": "",
            "input": "hi",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "person",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                }
            },
        }
    )
    with pytest.raises(ClientPayloadError) as exc_info:
        normalize_responses_request_payload(payload, openai_compat=False)
    err = exc_info.value
    assert err.code == "invalid_json_schema"
    assert err.error_type == "invalid_request_error"
    assert err.param == "text.format.schema"
    assert "person" in str(err)


def test_normalize_responses_payload_accepts_strict_false():
    payload = _json_object(
        {
            "model": "gpt-5.5",
            "instructions": "",
            "input": "hi",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "person",
                    "strict": False,
                    "schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                }
            },
        }
    )

    request = normalize_responses_request_payload(payload, openai_compat=False)
    assert request.text is not None
    assert request.text.format is not None
    assert request.text.format.strict is False


def test_normalize_responses_payload_accepts_valid_strict_schema():
    payload = _json_object(
        {
            "model": "gpt-5.5",
            "instructions": "",
            "input": "hi",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "person",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                        "required": ["name", "age"],
                        "additionalProperties": False,
                    },
                }
            },
        }
    )
    request = normalize_responses_request_payload(payload, openai_compat=False)
    assert request.text is not None
    assert request.text.format is not None
    assert request.text.format.strict is True


def test_chat_completions_strict_schema_violation_surfaces_via_enforce_helper():
    payload = {
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "person",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
    }
    request = ChatCompletionsRequest.model_validate(payload)
    responses_request = request.to_responses_request()
    with pytest.raises(ClientPayloadError) as exc_info:
        enforce_strict_text_format(responses_request)
    err = exc_info.value
    assert err.code == "invalid_json_schema"
    assert err.param == "text.format.schema"







def test_strict_function_tool_missing_additional_properties():
    schema: JsonValue = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    violation = validate_strict_function_tool_schema(schema, name="get_weather", param="tools[0].parameters")
    assert violation is not None
    assert violation.code == "invalid_function_parameters"
    assert violation.param == "tools[0].parameters"
    assert "Invalid schema for function 'get_weather'" in violation.message
    assert "additionalProperties" in violation.message


def test_strict_function_tool_additional_properties_true_rejected():
    schema: JsonValue = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": True,
    }
    violation = validate_strict_function_tool_schema(schema, name="get_weather", param="tools[0].parameters")
    assert violation is not None
    assert violation.code == "invalid_function_parameters"


def test_strict_function_tool_missing_required_rejected():
    schema: JsonValue = {
        "type": "object",
        "properties": {"city": {"type": "string"}, "unit": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    }
    violation = validate_strict_function_tool_schema(schema, name="get_weather", param="tools[0].parameters")
    assert violation is not None
    assert "required" in violation.message.lower()


def test_strict_function_tool_valid_schema_passes():
    schema: JsonValue = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    }
    assert validate_strict_function_tool_schema(schema, name="get_weather", param="tools[0].parameters") is None


def test_strict_function_tool_nested_violation_surfaced():
    schema: JsonValue = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],

            }
        },
        "required": ["filter"],
        "additionalProperties": False,
    }
    violation = validate_strict_function_tool_schema(schema, name="search", param="tools[0].parameters")
    assert violation is not None
    assert "filter" in violation.message


def test_strict_function_tool_anonymous_name_falls_back():
    schema: dict[str, JsonValue] = {"type": "object"}
    violation = validate_strict_function_tool_schema(schema, name=None, param="tools[0].parameters")
    assert violation is not None
    assert "Invalid schema for function 'function'" in violation.message







def _responses_payload_with_tools(tools: list[JsonValue]) -> dict[str, JsonValue]:
    return _json_object(
        {
            "model": "gpt-5.5",
            "instructions": "",
            "input": "hi",
            "tools": tools,
        }
    )


def test_enforce_strict_function_tools_rejects_missing_additional_properties():
    tools: list[JsonValue] = [
        {
            "type": "function",
            "name": "get_weather",
            "description": "x",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "strict": True,
        }
    ]
    with pytest.raises(ClientPayloadError) as exc_info:
        normalize_responses_request_payload(_responses_payload_with_tools(tools), openai_compat=False)
    err = exc_info.value
    assert err.code == "invalid_function_parameters"
    assert err.error_type == "invalid_request_error"
    assert err.param == "tools[0].parameters"
    assert "get_weather" in str(err)


def test_enforce_strict_function_tools_rejects_at_correct_index():
    tools: list[JsonValue] = [
        {
            "type": "function",
            "name": "compliant",
            "description": "x",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["a"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "broken",
            "description": "x",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],

            },
            "strict": True,
        },
    ]
    with pytest.raises(ClientPayloadError) as exc_info:
        normalize_responses_request_payload(_responses_payload_with_tools(tools), openai_compat=False)
    err = exc_info.value
    assert err.param == "tools[1].parameters"
    assert "broken" in str(err)


def test_enforce_strict_function_tools_accepts_strict_false():
    tools: list[JsonValue] = [
        {
            "type": "function",
            "name": "get_weather",
            "description": "x",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "strict": False,
        }
    ]

    normalize_responses_request_payload(_responses_payload_with_tools(tools), openai_compat=False)


def test_enforce_strict_function_tools_accepts_omitted_strict():
    tools: list[JsonValue] = [
        {
            "type": "function",
            "name": "get_weather",
            "description": "x",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]
    normalize_responses_request_payload(_responses_payload_with_tools(tools), openai_compat=False)


def test_enforce_strict_function_tools_accepts_compliant_schema():
    tools: list[JsonValue] = [
        {
            "type": "function",
            "name": "get_weather",
            "description": "x",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
            "strict": True,
        }
    ]
    request = normalize_responses_request_payload(_responses_payload_with_tools(tools), openai_compat=False)
    tool = _json_object(request.tools[0])
    assert tool["strict"] is True


def test_enforce_strict_function_tools_param_template_for_chat():




    chat_tools: list[JsonValue] = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "x",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
                "strict": True,
            },
        }
    ]
    with pytest.raises(ClientPayloadError) as exc_info:
        enforce_strict_function_tools_format(
            chat_tools,
            param_template="tools[{index}].function.parameters",
            nested=True,
        )
    assert exc_info.value.param == "tools[0].function.parameters"







def test_chat_function_tool_strict_true_preserved_in_coercion():
    payload = {
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "x",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            }
        ],
    }
    request = ChatCompletionsRequest.model_validate(payload)
    responses_request = request.to_responses_request()
    tool = _json_object(responses_request.tools[0])
    assert tool["strict"] is True


    enforce_strict_function_tools_format(
        request.tools,
        param_template="tools[{index}].function.parameters",
        nested=True,
    )


def test_chat_function_tool_strict_true_violation_pre_validates():
    payload = {
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "x",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],

                    },
                    "strict": True,
                },
            }
        ],
    }
    request = ChatCompletionsRequest.model_validate(payload)
    with pytest.raises(ClientPayloadError) as exc_info:
        enforce_strict_function_tools_format(
            request.tools,
            param_template="tools[{index}].function.parameters",
            nested=True,
        )
    err = exc_info.value
    assert err.code == "invalid_function_parameters"
    assert err.param == "tools[0].function.parameters"


def test_chat_function_tool_strict_false_is_preserved_as_false():
    payload = {
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "x",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    "strict": False,
                },
            }
        ],
    }
    request = ChatCompletionsRequest.model_validate(payload)
    responses_request = request.to_responses_request()
    tool = _json_object(responses_request.tools[0])
    assert tool.get("strict") is False


def test_chat_function_tool_without_strict_has_no_strict_key():
    payload = {
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "x",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
    }
    request = ChatCompletionsRequest.model_validate(payload)
    responses_request = request.to_responses_request()
    tool = responses_request.tools[0]
    assert isinstance(tool, dict)
    assert "strict" not in tool








def test_chat_strict_violation_param_uses_original_index_when_normalizer_drops():

    payload = {
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [

            {
                "type": "function",
                "function": {
                    "description": "no name",
                    "parameters": {"type": "object", "properties": {}},
                },
            },

            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "x",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    "strict": True,
                },
            },
        ],
    }
    request = ChatCompletionsRequest.model_validate(payload)
    with pytest.raises(ClientPayloadError) as exc_info:
        enforce_strict_function_tools_format(
            request.tools,
            param_template="tools[{index}].function.parameters",
            nested=True,
        )
    err = exc_info.value
    assert err.code == "invalid_function_parameters"



    assert err.param == "tools[1].function.parameters"


def test_chat_strict_enforcement_runs_against_raw_payload_in_handler_path():

    payload = {
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "x",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
        ],
    }
    request = ChatCompletionsRequest.model_validate(payload)

    enforce_strict_function_tools_format(
        request.tools,
        param_template="tools[{index}].function.parameters",
        nested=True,
    )









def test_chat_strict_violation_when_type_omitted_but_function_dict_present():

    chat_tools: list[JsonValue] = [
        {

            "function": {
                "name": "get_weather",
                "description": "x",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],

                },
                "strict": True,
            },
        }
    ]
    with pytest.raises(ClientPayloadError) as exc_info:
        enforce_strict_function_tools_format(
            chat_tools,
            param_template="tools[{index}].function.parameters",
            nested=True,
        )
    err = exc_info.value
    assert err.code == "invalid_function_parameters"
    assert err.param == "tools[0].function.parameters"
    assert "get_weather" in str(err)


def test_chat_strict_skips_tool_without_function_wrapper_even_with_type():

    chat_tools: list[JsonValue] = [
        {
            "type": "function",


            "name": "get_weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "strict": True,
        }
    ]


    enforce_strict_function_tools_format(
        chat_tools,
        param_template="tools[{index}].function.parameters",
        nested=True,
    )


def test_responses_path_still_requires_type_function():

    tools: list[JsonValue] = [
        {



            "name": "get_weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "strict": True,
        }
    ]


    enforce_strict_function_tools_format(tools)
