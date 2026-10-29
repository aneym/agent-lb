from __future__ import annotations

import os
import re
from pathlib import Path


def verify_factory(SOURCE, source_table, classes, check, read, frontmatter_model):
    def factory(rule: str, valid: bool | None, detail: str) -> None:
        check(valid, f"factory:{rule} {detail}")

    DEFAULT_HEADS = {
        "sonnet-implementer": {
            "seat": "sonnet-implementer",
            "model": "sonnet-latest",
            "vendor": "anthropic",
            "effort": "high",
        },
        "gpt-implementer": {"seat": "gpt-implementer", "model": "sol-latest", "vendor": "openai", "effort": "medium"},
    }
    implement_default = source_table.get("implement_default")
    head_rows = [
        [
            {key: entry.get(key) for key in ("seat", "model", "vendor", "effort")}
            for entry in classes.get(name, {}).get("chain", [])
            if isinstance(entry, dict)
        ]
        for name in ("implement", "mechanical")
    ]
    factory(
        "implement-head",
        implement_default in DEFAULT_HEADS
        and all(
            DEFAULT_HEADS[implement_default] in rows
            and all(row in rows for row in DEFAULT_HEADS.values())
            and rows[0] in DEFAULT_HEADS.values()
            for rows in head_rows
        ),
        f"implement and mechanical hold both default implementers with one at the head; implement_default={implement_default}",
    )
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
    expected_auditors: dict = {
        vendor: {"seat": "verifier", "model": "sonnet-latest", "vendor": "anthropic", "effort": "high"}
        for vendor in ("openai", "cursor", "devin", "glm", "kimi")
    }
    expected_auditors["xai"] = {
        **expected_auditors["openai"],
        "then": {"seat": "codex-verifier", "model": "sol-latest", "vendor": "openai", "effort": "high"},
    }
    expected_auditors["anthropic"] = {
        "seat": "codex-verifier",
        "model": "sol-latest",
        "vendor": "openai",
        "effort": "high",
    }
    for task_class in ("implement", "mechanical"):
        audit = classes.get(task_class, {}).get("audit", {})
        auditors = audit.get("by_author_vendor", {})
        factory(
            "audit-cross-vendor",
            audit.get("required") is False
            and audit.get("mode") == "on_request"
            and all(audit.get(key) is True for key in ("fresh_per_unit", "release_facts"))
            and auditors == expected_auditors
            and all(author != auditor.get("vendor") for author, auditor in auditors.items()),
            f"{task_class} names fresh cross-vendor auditors, on request only",
        )
    review = source_table.get("policy", {}).get("review", {})
    factory(
        "review-policy",
        classes.get("verify", {}).get("cross_vendor") is True
        and any(
            entry.get("seat") == "codex-verifier" and entry.get("effort") == "high"
            for entry in classes.get("verify", {}).get("chain", [])
        )
        and review.get("timing") == "on_request"
        and review.get("money_path") == {"lenses": 1, "timing": "on_request"}
        and all(review.get(key) is True for key in ("fresh_per_unit", "cross_vendor"))
        and all(review.get(key) is False for key in ("never_after_fail", "release_facts"))
        and review.get("max_fix_rounds") == 1
        and source_table.get("standards", {}).get("verify", {}).get("money_path_effort") == "xhigh",
        "cross-vendor review on request only, money path included, and one fix round",
    )
    bands = source_table.get("policy", {}).get("stage_effort", {}).get("bands", {})
    forwarders = {
        "codex-sol",
        "codex-verifier",
        "codex-test-runner",
        "computer-use",
        "cursor-seat",
        "devin-seat",
        "muse-seat",
        "muse-seat-local",
        "sonnet-implementer",
        "verifier",
        "gpt-implementer",
        "terra-implementer",
        "grok-implementer",
        "gpt-explorer",
        "codex-sol-local",
        "codex-test-runner-local",
        "codex-verifier-local",
        "computer-use-local",
        "computer-use-terra-local",
        "verifier-local",
        "gpt-explorer-local",
    }
    VENDORS = ("anthropic", "openai", "cursor", "devin", "glm", "kimi", "muse")
    BOX_ADAPTERS = {"codex": "openai", "claude-code": "anthropic", "cursor": "cursor", "devin": "devin"}
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
        if entry.get("seat", "").startswith("gpt-") or entry.get("seat") == "sol-consult":
            if not re.fullmatch(r"(?:sol|luna|astra)-latest-(?:low|medium|high|xhigh)", model):
                return False
        if re.fullmatch(r"(?:sol|luna|astra)-latest-(?:low|medium|high|xhigh)", model):
            return model.rsplit("-", 1)[-1] == effort
        return field is None or field == effort

    effort_valid = all(
        valid_effort(entry, task_class) and (entry.get("vendor") in ("openai", "anthropic") or "effort" not in entry)
        for task_class, spec in classes.items()
        for entry in spec.get("chain", [])
    )
    effort_valid = effort_valid and all(
        valid_effort(auditor, "verify")
        for spec in classes.values()
        for auditor in spec.get("audit", {}).get("by_author_vendor", {}).values()
    )
    second = classes.get("plan", {}).get("second_opinion", {})
    effort_valid = effort_valid and valid_effort(second, "plan")
    factory("stage-effort", effort_valid, "chain, audit and second-opinion efforts match their stage and definition")
    factory(
        "second-opinion",
        second
        == {
            "seat": "astra-consult",
            "model": "astra-latest",
            "vendor": "openai",
            "effort": "high",
            "when": "complex or risky plans, before the work is split",
        },
        "complex plans get astra-consult/astra-latest/openai/high",
    )
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
    seats.update(
        auditor.get("seat")
        for spec in classes.values()
        for auditor in spec.get("audit", {}).get("by_author_vendor", {}).values()
    )
    seats.add(second.get("seat"))
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
        f"source defines every chain, auditor and second-opinion seat; missing {missing}",
    )

    seat_map = source_table.get("seats")
    problems: list[str] = []
    if not isinstance(seat_map, dict) or not seat_map:
        problems.append("seats must be a non-empty object")
        seat_map = {}
    class_entries = []
    auditor_seats = set()
    for task_class, spec in classes.items():
        for entry in spec.get("chain", []):
            if isinstance(entry, dict) and isinstance(entry.get("seat"), str):
                class_entries.append((task_class, entry))
        opinion = spec.get("second_opinion")
        if isinstance(opinion, dict) and isinstance(opinion.get("seat"), str):
            class_entries.append((task_class, opinion))
        for entry in spec.get("audit", {}).get("by_author_vendor", {}).values():
            if isinstance(entry, dict) and isinstance(entry.get("seat"), str):
                class_entries.append((task_class, entry))
                auditor_seats.add(entry["seat"])
    class_seats = {entry["seat"] for _, entry in class_entries}
    definitions = {path.stem for path in (SOURCE / "agents").glob("*.md")}
    standard_definitions = set(stage_definitions)
    if set(seat_map) | standard_definitions != definitions:
        problems.append(
            f"definitions differ: missing {sorted(definitions - set(seat_map) - standard_definitions)}, "
            f"extra {sorted((set(seat_map) | standard_definitions) - definitions)}"
        )
    named = class_seats | off_seats | {classes.get("implement", {}).get("escalation", {}).get("seat")}
    for name in named - set(seat_map):
        problems.append(f"missing referenced seat {name}")
    if any(isinstance(value, dict) and value.get("needs_test_run") is True for value in seat_map.values()):
        if "codex-test-runner" not in seat_map:
            problems.append("needs_test_run requires codex-test-runner")
    for name, value in seat_map.items():
        if not isinstance(name, str) or not SEAT_NAME.fullmatch(name):
            problems.append(f"invalid seat name {name!r}")
            continue
        if not isinstance(value, dict):
            problems.append(f"{name} is not an object")
            continue
        allowed = {"vendor", "box", "model", "effort", "needs_test_run", "maker", "roles"}
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
        if name in auditor_seats:
            if type(value.get("needs_test_run")) is not bool:
                problems.append(f"{name} auditor needs boolean needs_test_run")
        elif "needs_test_run" in value:
            problems.append(f"{name} is not an auditor")
        if name in class_seats:
            if ("model" in value or "effort" in value) and not all(
                value.get("model") == entry.get("model") and value.get("effort") == entry.get("effort")
                for _, entry in class_entries
                if entry["seat"] == name
            ):
                problems.append(f"{name} class seat has conflicting model or effort")
        else:
            if name == "sol-consult" and set(value) == {"vendor", "box"}:
                value = value | {"model": "sol-latest", "effort": "high"}
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

    escalation = classes.get("implement", {}).get("escalation")
    implement = classes.get("implement", {})
    chain = implement.get("chain", [])
    review = source_table.get("policy", {}).get("review", {})
    valid_escalation = isinstance(escalation, dict) and set(escalation) == {"seat", "at_fix_round", "since", "evidence"}
    if valid_escalation:
        target = escalation["seat"]
        valid_escalation = (
            isinstance(target, str)
            and target in seat_map
            and bool(chain)
            and any(entry.get("seat") == target for entry in chain)
            and target != chain[0].get("seat")
            and isinstance(seat_map.get(target), dict)
            and seat_map[target].get("vendor") in implement.get("audit", {}).get("by_author_vendor", {})
            and type(escalation["at_fix_round"]) is int
            and escalation["at_fix_round"] >= 1
            and isinstance(escalation["since"], str)
            and re.fullmatch(r"20\d\d-\d\d-\d\d", escalation["since"])
            and isinstance(escalation["evidence"], str)
            and bool(escalation["evidence"].strip())
        )
    factory("escalation", valid_escalation, "implement escalates to an audited chain seat at a fix round of 1 or more")

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
