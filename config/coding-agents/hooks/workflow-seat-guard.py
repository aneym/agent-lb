#!/usr/bin/env python3
"""Check statically visible Workflow agent options; ambiguous scripts fail open."""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import re
import sys


class ParseError(ValueError):
    pass


def literal_end(script: str, start: int, interpolations: list[str] | None = None) -> int:
    """Consume a literal, collecting template code separately from literal text."""
    quote = script[start]
    i = start + 1
    while i < len(script):
        if script[i] == '\\':
            i += 2
        elif script[i] == quote:
            return i + 1
        elif quote == '`' and script.startswith('${', i):
            i += 2
            body_start = i
            depth = 1
            while i < len(script) and depth:
                if script[i] in "\"'`":
                    i = literal_end(script, i)
                elif script.startswith('//', i):
                    end = script.find('\n', i)
                    i = len(script) if end < 0 else end + 1
                elif script.startswith('/*', i):
                    end = script.find('*/', i + 2)
                    if end < 0:
                        raise ParseError('unterminated comment')
                    i = end + 2
                elif script[i] == '/' and script[:i].rstrip()[-1:] in ('(', '=', ':', ','):
                    i += 1
                    in_class = False
                    while i < len(script):
                        if script[i] == '\\':
                            i += 2
                            continue
                        if script[i] == '[':
                            in_class = True
                        elif script[i] == ']':
                            in_class = False
                        elif script[i] == '/' and not in_class:
                            i += 1
                            break
                        i += 1
                    else:
                        raise ParseError('unterminated regex in template')
                else:
                    if script[i] == '{':
                        depth += 1
                    elif script[i] == '}':
                        depth -= 1
                    i += 1
            if depth:
                raise ParseError('unterminated template interpolation')
            if interpolations is not None:
                interpolations.append(script[body_start:i - 1])
        else:
            i += 1
    raise ParseError(f'unterminated string at {start}')


def tokens(script: str, interpolations: list[str]) -> list[tuple[str, str]]:
    """Tokenize code and collect template expressions for recursive inspection."""
    result: list[tuple[str, str]] = []
    i = 0
    while i < len(script):
        c = script[i]
        if c.isspace():
            i += 1
            continue
        if script.startswith('//', i):
            end = script.find('\n', i)
            i = len(script) if end < 0 else end + 1
            continue
        if script.startswith('/*', i):
            end = script.find('*/', i + 2)
            if end < 0:
                raise ParseError('unterminated comment')
            i = end + 2
            continue
        if c in "\"'`":
            quote, start = c, i
            i = literal_end(script, start, interpolations)
            raw = script[start + 1:i - 1]
            # Escaped values are not safe to interpret as static options.
            result.append(('string' if quote != '`' and '\\' not in raw else 'opaque', raw))
            continue
        if c == '/' and (not result or result[-1][1] in ('=', '(', '[', '{', ',', ':', '!', '?', ';', 'return', '=>', '&&', '||')):
            i += 1
            in_class = False
            while i < len(script):
                if script[i] == '\\':
                    i += 2
                    continue
                if script[i] == '[':
                    in_class = True
                elif script[i] == ']':
                    in_class = False
                elif script[i] == '/' and not in_class:
                    i += 1
                    while i < len(script) and script[i].isalpha():
                        i += 1
                    break
                i += 1
            else:
                raise ParseError('unterminated regex')
            result.append(('opaque', 'regex'))
            continue
        match = re.match(r'[A-Za-z_$][\w$]*|\.\.\.|=>|&&|\|\|', script[i:])
        if match:
            word = match.group()
            result.append(('word', word))
            i += len(word)
        else:
            result.append(('punct', c))
            i += 1
    return result


def brackets(items: list[tuple[str, str]]) -> dict[int, int]:
    stack: list[int] = []
    pairs: dict[int, int] = {}
    closing = {')': '(', ']': '[', '}': '{'}
    for i, (kind, value) in enumerate(items):
        if kind != 'punct':
            continue
        if value in ('(', '[', '{'):
            stack.append(i)
        elif value in closing:
            if not stack or items[stack[-1]][1] != closing[value]:
                raise ParseError('unbalanced brackets')
            start = stack.pop()
            pairs[start] = i
    if stack:
        raise ParseError('unclosed brackets')
    return pairs


def split(items: list[tuple[str, str]], pairs: dict[int, int], start: int, end: int) -> list[tuple[int, int]]:
    parts = []
    first = start
    while start < end:
        if start in pairs:
            start = pairs[start] + 1
        elif items[start] == ('punct', ','):
            parts.append((first, start))
            start += 1
            first = start
        else:
            start += 1
    parts.append((first, end))
    return parts


def is_retired(value: str, table: dict) -> bool:
    if value in ('sonnet', 'haiku'):
        pinned = table.get('aliases', {}).get(value + '-latest', {}).get('pinned', value)
        value = os.environ.get('ANTHROPIC_DEFAULT_' + value.upper() + '_MODEL') or pinned
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in table['retired'])


def retired_in_args(value, table: dict, path: str = 'args') -> str | None:
    """Seat options a script reads from Workflow args (`route workflow-args`) never reach the static scan.

    Only a `model` beside a seat key counts, so data that merely mentions an old model passes."""
    if isinstance(value, dict):
        seat_like = any(key in value for key in ('agentType', 'seat', 'implementer'))
        for key, item in value.items():
            if seat_like and key == 'model' and isinstance(item, str) and is_retired(item, table):
                return (f'Workflow args pin retired model {item!r} at {path}.model: '
                        'regenerate them with `route workflow-args` (models.md, Workflows)')
            found = retired_in_args(item, table, f'{path}.{key}')
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = retired_in_args(item, table, f'{path}[{index}]')
            if found:
                return found
    return None


def inspect(script: str, table: dict) -> tuple[str | None, bool]:
    interpolations: list[str] = []
    items = tokens(script, interpolations)
    pairs = brackets(items)
    reasons = []
    warning = False
    retired = table['retired']
    if not isinstance(retired, list) or not all(isinstance(p, str) for p in retired):
        raise ParseError('invalid retired patterns')
    for i, item in enumerate(items[:-1]):
        if item != ('word', 'agent') or items[i + 1] != ('punct', '('):
            continue
        args = split(items, pairs, i + 2, pairs[i + 1])
        if any(a < b and items[a][0] == 'word' and items[a][1] == '...' for a, b in args):
            warning = True
            continue
        if len(args) < 2 or args[1][0] == args[1][1]:
            reasons.append('agent() without agentType: pass agentType (models.md, Workflows)')
            continue
        start, end = args[1]
        if items[start] != ('punct', '{') or pairs.get(start) != end - 1:
            warning = True
            continue
        fields = split(items, pairs, start + 1, end - 1)
        if any(a < b and items[a][0] == 'word' and items[a][1] == '...' for a, b in fields):
            warning = True
            continue
        keys = {}
        uncertain = False
        for a, b in fields:
            if a == b:
                continue
            kind, key = items[a]
            if kind not in ('word', 'string'):
                uncertain = True
                continue
            if a + 1 == b:  # shorthand property
                keys[key] = None
            elif items[a + 1] == ('punct', ':'):
                keys[key] = items[a + 2] if a + 3 == b else None
            else:
                uncertain = True
        if uncertain:
            warning = True
            continue
        if 'agentType' not in keys:
            reasons.append('agent() without agentType: pass agentType (models.md, Workflows)')
        model = keys.get('model')
        if model and model[0] == 'string' and is_retired(model[1], table):
            reasons.append(f'agent() pins retired model {model[1]!r}: use a current model alias (models.md, Workflows)')
    for body in interpolations:
        reason, body_warning = inspect(body, table)
        if reason:
            reasons.append(reason)
        warning = warning or body_warning
    return (reasons[0] if reasons else None), warning


def main() -> None:
    output = {'hookEventName': 'PreToolUse'}
    try:
        payload = json.load(sys.stdin)
        tool_input = payload['tool_input']
        script = tool_input.get('script')
        if script is None:
            script = Path(tool_input['scriptPath']).read_text()
        table_path = Path(os.environ.get('ROUTE_TABLE') or Path.home() / '.agent-lb/managed/coding-agents/routing-table.json')
        if not table_path.exists() and 'ROUTE_TABLE' not in os.environ:
            table_path = Path(__file__).resolve().parent.parent / 'routing-table.json'
        table = json.loads(table_path.read_text())
        reason, warning = inspect(script, table)
        reason = reason or retired_in_args(tool_input.get('args'), table)
        if reason:
            output.update(permissionDecision='deny', permissionDecisionReason=reason)
        elif warning:
            output['additionalContext'] = 'workflow-seat-guard: dynamic agent() options could not be checked; pass agentType and avoid retired model pins (models.md, Workflows).'
    except Exception:
        output['additionalContext'] = 'workflow-seat-guard: script or routing table could not be parsed; dispatch was not blocked.'
    print(json.dumps({'hookSpecificOutput': output}))


if __name__ == '__main__':
    main()
