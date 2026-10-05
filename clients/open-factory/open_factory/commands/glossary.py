"""Check that glossary documentation explains each term completely."""

import json
import re
from pathlib import Path


def build_parser(subparsers):
    parser = subparsers.add_parser("glossary", help="Check glossary entry completeness")
    commands = parser.add_subparsers(dest="glossary_command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--file", default="CONTEXT.md")
    check.set_defaults(func=run)


def entries(text):
    blocks = re.findall(r"```terms\s*\n(.*?)```", text, re.S)
    if blocks:
        result = []
        for block in blocks:
            data = json.loads(block)
            if isinstance(data, dict):
                data = data.get("terms", [data])
            if not isinstance(data, list) or any(not isinstance(t, dict) for t in data):
                raise ValueError("terms fences must contain glossary objects")
            result.extend(data)
        return result
    result = []
    bold = re.split(r"^\*\*(.+?)\*\*:\s*$", text, flags=re.M)
    if len(bold) > 1:
        for name, body in zip(bold[1::2], bold[2::2]):
            body = re.split(r"^#{2,3}\s", body, maxsplit=1, flags=re.M)[0]
            fields = re.split(r"^(Example|Not|See|Code):\s*", body, flags=re.M)
            item = {"term": name, "definition": fields[0].strip()}
            for key, value in zip(fields[1::2], fields[2::2]):
                item[{"See": "related"}.get(key, key.lower())] = value.strip()
            result.append(item)
        return result
    sections = re.split(r"^##\s+(.+?)\s*$", text, flags=re.M)
    for name, body in zip(sections[1::2], sections[2::2]):
        item = {"term": name}
        for field, value in re.findall(
            r"^\s*(?:[-*]\s*)?(?:\*\*)?"
            r"(Definition|Example|Not|Related(?: terms)?|Code(?: name)?)(?:\*\*)?\s*:\s*"
            r"(.*?)(?=^\s*(?:[-*]\s*)?(?:\*\*)?"
            r"(?:Definition|Example|Not|Related(?: terms)?|Code(?: name)?)(?:\*\*)?\s*:|\Z)",
            body,
            flags=re.M | re.S | re.I,
        ):
            key = field.lower().split()[0]
            item[key] = value.strip()
        result.append(item)
    return result


def run(args):
    try:
        terms = entries(Path(args.file).read_text())
        failures = []
        if not terms:
            failures.append("no glossary entries found")
        for term in terms:
            name = term.get("term", term.get("name", "<unnamed>"))
            definition = term.get("definition", "")
            if not isinstance(definition, str) or not definition.strip() or len(definition.split()) > 25:
                failures.append(f"{name}: definition must contain 1–25 words")
            for field, aliases in [
                ("example", ("example", "examples")),
                ("not", ("not",)),
                ("related", ("related", "related_terms")),
                ("code", ("code", "code_name")),
            ]:
                if not any(term.get(key) for key in aliases):
                    failures.append(f"{name}: missing {field}")
        for failure in failures:
            print(failure)
        if not failures:
            print(f"{len(terms)} glossary entries complete")
        return int(bool(failures))
    except (OSError, ValueError, TypeError) as exc:
        print(f"glossary: {exc}")
        return 1
