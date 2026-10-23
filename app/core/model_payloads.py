from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode

from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header


def rewrite_models(raw: bytes, content_type: bytes, rewrite: Callable[[str], str]) -> bytes:
    media_type, options = parse_options_header(content_type)
    if media_type == b"multipart/form-data":
        boundary = options.get(b"boundary")
        if not boundary:
            return raw
        spans: list[tuple[int, int]] = []
        field = bytearray()
        value = bytearray()
        disposition = b""
        model_part = False

        def part_begin() -> None:
            nonlocal disposition, model_part
            disposition = b""
            model_part = False

        def header_field(data: bytes, start: int, end: int) -> None:
            field.extend(data[start:end])

        def header_value(data: bytes, start: int, end: int) -> None:
            value.extend(data[start:end])

        def header_end() -> None:
            nonlocal disposition
            if field.lower() == b"content-disposition":
                disposition = bytes(value)
            field.clear()
            value.clear()

        def headers_finished() -> None:
            nonlocal model_part
            _, params = parse_options_header(disposition)
            model_part = params.get(b"name") == b"model" and b"filename" not in params

        def part_data(data: bytes, start: int, end: int) -> None:
            if model_part:
                if spans and spans[-1][1] == start:
                    spans[-1] = (spans[-1][0], end)
                else:
                    spans.append((start, end))

        parser = MultipartParser(
            boundary,
            {
                "on_part_begin": part_begin,
                "on_header_field": header_field,
                "on_header_value": header_value,
                "on_header_end": header_end,
                "on_headers_finished": headers_finished,
                "on_part_data": part_data,
            },
        )
        try:
            parser.write(raw)
            parser.finalize()
        except MultipartParseError:
            return raw
        replacements = [(start, end, rewrite(raw[start:end].decode(errors="replace")).encode()) for start, end in spans]
        for start, end, replacement in reversed(replacements):
            raw = raw[:start] + replacement + raw[end:]
        return raw
    if media_type == b"application/x-www-form-urlencoded":
        fields = parse_qsl(raw.decode(errors="replace"), keep_blank_values=True)
        return urlencode([(key, rewrite(value) if key == "model" else value) for key, value in fields]).encode()
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw
    if isinstance(body, dict) and isinstance(body.get("model"), str):
        target = rewrite(body["model"])
        if target != body["model"]:
            body["model"] = target
            return json.dumps(body).encode()
    return raw
