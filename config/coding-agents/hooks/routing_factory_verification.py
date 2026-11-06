from __future__ import annotations

import os
import re
from pathlib import Path


def verify_factory(SOURCE, source_table, classes, check, read, frontmatter_model):
    def factory(rule: str, valid: bool | None, detail: str) -> None:
        check(valid, f"factory:{rule} {detail}")

    off_default = source_table.get("off_default", [])
    off_seats = {item.get("seat") for item in off_default}
    factory(
        "off-default",
        all(all(item.get(field) for field in ("seat", "since", "evidence", "returns_when")) for item in off_default)
        and not any(
            entry.get("seat") in off_seats | {"luna-implementer"}
            for spec in classes.values()
            for entry in spec.get("chain", [])
        ),
        "dated off-default seats are absent from chains",
    )
    bands = source_table.get("policy", {}).get("stage_effort", {}).get("bands", {})
    forwarders = {"computer-use-terra-local"}
    VENDORS = ("anthropic", "openai")
    BOX_ADAPTERS = {"codex": "openai", "claude-code": "anthropic"}
    EFFORTS = ("low", "medium", "high", "xhigh")
    SEAT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    def valid_effort(entry: dict, band: str) -> bool:
        effort = entry.get("effort")
        if entry.get("vendor") in ("openai", "anthropic") and effort not in bands.get(band, []):
            return False
        text = read(SOURCE / "agents" / f"{entry.get('seat')}.md")
        head = text.split("\n---", 1)[0]
        field = next((line.split(":", 1)[1].strip() for line in head.splitlines() if line.startswith("effort:")), None)
        if entry.get("seat") in forwarders:
            return field in bands.get("relay", [])
        model = frontmatter_model(text) or ""
        if re.fullmatch(r"(?:sol|luna|astra)-latest-(?:low|medium|high|xhigh)", model):
            return model.rsplit("-", 1)[-1] == effort
        return field is None or field == effort

    effort_valid = all(
        valid_effort(entry, task_class) and (entry.get("vendor") in ("openai", "anthropic") or "effort" not in entry)
        for task_class, spec in classes.items()
        for entry in spec.get("chain", [])
    )
    factory("stage-effort", effort_valid, "chain efforts match their stage and definition")
    routing_sections = sorted((SOURCE / "routing").glob("*.md"))
    routing_text = "\n".join(read(path) for path in routing_sections)
    factory(
        "routing-doc",
        bool(routing_text)
        and all(path.stat().st_size <= 20000 for path in routing_sections)
        and all(re.search(r"20\d\d-\d\d-\d\d", line) for line in routing_text.splitlines() if line.startswith("## ")),
        "routing section headings are dated and sections are below 20000 bytes",
    )
    seats = {entry.get("seat") for spec in classes.values() for entry in spec.get("chain", [])}
    stage_definitions = {
        "judge": ("opus", "medium"),
        "decider": ("sonnet", "high"),
        "digest": ("sonnet", "medium"),
        "jev": ("haiku", "low"),
    }
    seats.update(stage_definitions)
    for name, (model, effort) in stage_definitions.items():
        text = read(SOURCE / "agents" / f"{name}.md")
        head = text.split("\n---", 1)[0]
        factory(
            "stage-definitions",
            frontmatter_model(text) == model and f"effort: {effort}" in head.splitlines(),
            f"{name} uses {model} at {effort} effort before installer model resolution",
        )
    missing = sorted(seat for seat in seats if not seat or not (SOURCE / "agents" / f"{seat}.md").is_file())
    factory(
        "seat-definitions",
        not missing,
        f"source defines every chain seat; missing {missing}",
    )

    seat_map = source_table.get("seats")
    problems: list[str] = []
    if not isinstance(seat_map, dict) or not seat_map:
        problems.append("seats must be a non-empty object")
        seat_map = {}
    class_entries = []
    for task_class, spec in classes.items():
        for entry in spec.get("chain", []):
            if isinstance(entry, dict) and isinstance(entry.get("seat"), str):
                class_entries.append((task_class, entry))
    class_seats = {entry["seat"] for _, entry in class_entries}
    definitions = {path.stem for path in (SOURCE / "agents").glob("*.md")}
    standard_definitions = set(stage_definitions)
    if set(seat_map) | standard_definitions != definitions:
        problems.append(
            f"definitions differ: missing {sorted(definitions - set(seat_map) - standard_definitions)}, "
            f"extra {sorted((set(seat_map) | standard_definitions) - definitions)}"
        )
    for name in (class_seats | off_seats) - set(seat_map):
        problems.append(f"missing referenced seat {name}")
    for name, value in seat_map.items():
        if not isinstance(name, str) or not SEAT_NAME.fullmatch(name):
            problems.append(f"invalid seat name {name!r}")
            continue
        if not isinstance(value, dict):
            problems.append(f"{name} is not an object")
            continue
        allowed = {"vendor", "box", "model", "effort"}
        if not {"vendor", "box"} <= value.keys() or value.keys() - allowed:
            problems.append(f"{name} has invalid fields")
        if value.get("vendor") not in VENDORS:
            problems.append(f"{name} has invalid vendor")
        box = value.get("box")
        if box is not None and (
            not isinstance(box, dict)
            or set(box) != {"adapter"}
            or not isinstance(box.get("adapter"), str)
            or BOX_ADAPTERS.get(box["adapter"]) != value.get("vendor")
        ):
            problems.append(f"{name} has invalid box adapter")
        if name in class_seats:
            if ("model" in value or "effort" in value) and not all(
                value.get("model") == entry.get("model") and value.get("effort") == entry.get("effort")
                for _, entry in class_entries
                if entry["seat"] == name
            ):
                problems.append(f"{name} class seat has conflicting model or effort")
        else:
            if value.get("model") not in source_table.get("aliases", {}) or value.get("effort") not in EFFORTS:
                problems.append(f"{name} needs model alias and effort")
            if name not in forwarders and (SOURCE / "agents" / f"{name}.md").is_file():
                head = read(SOURCE / "agents" / f"{name}.md").split("\n---", 1)[0]
                fields = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
                model = fields.get("model", "").strip()
                match = re.fullmatch(r"(sol|luna|astra)-latest-(low|medium|high|xhigh)", model)
                if match and (
                    value.get("model") != match.group(1) + "-latest" or value.get("effort") != match.group(2)
                ):
                    problems.append(f"{name} model effort disagrees with definition")
                elif not match and "effort" in fields and value.get("effort") != fields["effort"].strip():
                    problems.append(f"{name} effort disagrees with definition")
    for task_class, entry in class_entries:
        name = entry["seat"]
        if isinstance(seat_map.get(name), dict) and seat_map[name].get("vendor") != entry.get("vendor"):
            problems.append(f"{name} vendor disagrees with {task_class}")
    factory(
        "seats-map",
        not problems,
        "seats map: " + "; ".join(problems[:5])
        if problems
        else "legacy seat entries agree with chains and definitions; standard-only stage definitions are present",
    )

    private_literals: list[str] = []
    literals_error = ""
    literals_path = os.environ.get("AGENT_LB_PRIVATE_LITERALS")
    if literals_path:
        try:
            private_literals = [
                item.strip() for item in Path(literals_path).expanduser().read_text().splitlines() if item.strip()
            ]
        except OSError as exc:
            literals_error = f"cannot read AGENT_LB_PRIVATE_LITERALS: {type(exc).__name__}"
        if not private_literals and not literals_error:
            literals_error = "AGENT_LB_PRIVATE_LITERALS names an empty file"
    factory("private-literals", not literals_error, literals_error or "operator literal list loaded or not configured")
    public_pattern = re.compile(
        "|".join(
            [
                r"/Users/[A-Za-z0-9._-]|/Volumes/[A-Za-z0-9._-]|\.ts\.net\b|[Tt]ailscale|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"
            ]
            + [rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])" for literal in private_literals]
        )
    )
    public_files = [SOURCE / name for name in ("ROUTING.md", "claude-adapter.md", "install-policy.py")]
    public_files += sorted((SOURCE / "routing-table").glob("*.json"))
    public_files += sorted((SOURCE / "agents").glob("*.md")) + sorted((SOURCE / "hooks").glob("*.py"))
    private_lines = [
        f"{path.name}:{index}"
        for path in public_files
        for index, line in enumerate(read(path).splitlines(), 1)
        if public_pattern.search(line)
    ]
    factory("public-text", not private_lines, f"public policy has no private literals; found {private_lines}")

    return routing_sections
