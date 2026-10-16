from __future__ import annotations

import json
import re
from pathlib import Path

from tests.unit.test_route_ladder import CANONICAL_TABLE, REPO, route, setup, table_copy

SCHEMAS = REPO / "config" / "coding-agents" / "schemas"
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def schema_errors(value, schema: dict, root: dict, where: str = "$") -> list[str]:
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return schema_errors(value, target, root, where)
    errors = []
    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        ok = any((kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
                 or (kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
                 or (kind in TYPES and isinstance(value, TYPES[kind])) for kind in kinds)
        if not ok:
            return [f"{where}: {value!r} is not {kinds}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: {value!r} != {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: too short")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: above {schema['maximum']}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors += schema_errors(item, schema["items"], root, f"{where}[{index}]")
    if isinstance(value, dict):
        errors += [f"{where}: missing {key}" for key in schema.get("required", []) if key not in value]
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                errors += schema_errors(item, properties[key], root, f"{where}.{key}")
            elif extra is False:
                errors.append(f"{where}: unexpected {key}")
            elif isinstance(extra, dict):
                errors += schema_errors(item, extra, root, f"{where}.{key}")
    return errors


def assert_valid(value, name: str) -> None:
    schema = json.loads((SCHEMAS / name).read_text())
    assert schema_errors(value, schema, schema) == []


def dispatch_env(tmp_path: Path, *, claude_headroom: float | None = None) -> dict[str, str]:
    env = setup(tmp_path, codex_low=False)
    if claude_headroom is not None:
        pools_file = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
        pools = json.loads(pools_file.read_text())
        for pool in pools["pools"]:
            if pool["id"] == "anthropic-general":
                pool["headroomPercent"] = claude_headroom
        pools_file.write_text(json.dumps(pools))
    env["ROUTE_DECISIONS_LOG"] = str(tmp_path / "route-decisions.jsonl")
    return env


def dispatch(env: dict[str, str], table: Path, *args: str) -> dict:
    result = route(env, table, "dispatch", *args, "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def logged(env: dict[str, str]) -> list[dict]:
    path = Path(env["ROUTE_DECISIONS_LOG"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_opus_authority_classes_wait_under_the_claude_reserve_and_never_take_sol(tmp_path: Path) -> None:
    env = dispatch_env(tmp_path, claude_headroom=31.0)
    for task_class in ("plan", "review", "judge"):
        result = route(env, CANONICAL_TABLE, "dispatch", task_class, "--json")
        assert result.returncode == 3, result.stderr
        waited = json.loads(result.stdout)
        assert waited["status"] == "wait"
        assert "headroom 31% (keep 40%)" in waited["reason"]
        assert "sol" not in json.dumps({k: v for k, v in waited.items() if k != "reason"})
    assert logged(env) == []

    implement = dispatch(env, CANONICAL_TABLE, "implement")
    assert (implement["seat"], implement["model"]) == ("gpt-implementer", "gpt-6.1-sol")


def test_with_explore_off_every_class_takes_its_standard(tmp_path: Path) -> None:
    table = json.loads(CANONICAL_TABLE.read_text())
    assert_valid(table, "routing-table.v2.json")
    assert table["explore"]["enabled"] is False
    assert "judge" not in table["explore"]["arms"]
    env = dispatch_env(tmp_path)

    for task_class, standard in table["standards"].items():
        row = dispatch(env, CANONICAL_TABLE, task_class)
        assert (row["arm"], row["arm_id"], row["propensity"]) == ("default", "standard", 1.0), task_class
        assert (row["seat"], row["model_alias"], row["effort"]) == (
            standard["seat"], standard["model"], standard["effort"]), task_class
        assert row["policy_rev"] == "2026-10-16.1"
        assert_valid(row, "route-decision.v1.json")
    assert len(logged(env)) == len(table["standards"])

    relay = dispatch(env, CANONICAL_TABLE, "relay")
    assert relay["wrapper_model"] == "claude-sonnet-5-5"
    review = dispatch(env, CANONICAL_TABLE, "verify", "--author-vendor", "anthropic")
    assert (review["seat"], review["model"], review["effort"]) == ("codex-verifier", "gpt-6.1-sol", "high")
    plan = dispatch(env, CANONICAL_TABLE, "plan", "--dry-run")
    assert (plan["model"], plan["effort"]) == ("claude-opus-5-5", "medium")
    assert len(logged(env)) == len(table["standards"]) + 2


def test_an_explore_draw_is_logged_with_its_propensity_and_sticks_to_the_job(tmp_path: Path) -> None:
    def open_muse(table: dict) -> None:
        table["explore"].update(enabled=True, max_share_per_class=1.0)
        table["explore"]["arms"]["implement"][0]["share"] = 1.0

    table = table_copy(tmp_path, "muse-open", open_muse)
    env = dispatch_env(tmp_path)

    first = dispatch(env, table, "implement", "--job", "job-s1", "--seed", "7", "--caller", "test")
    assert (first["arm"], first["arm_id"], first["seat"], first["propensity"]) == ("explore", "muse", "muse-seat", 1.0)
    assert first["model"] == "muse-wonder-1.0"
    assert first["eligible_arms"] == ["muse"]
    assert (first["sticky"], first["attempt"]) == (False, 1)
    assert_valid(first, "route-decision.v1.json")

    again = dispatch(env, table, "implement", "--job", "job-s1")
    assert (again["arm"], again["arm_id"], again["seat"], again["model"]) == (
        "explore", "muse", "muse-seat", "muse-wonder-1.0")
    assert (again["sticky"], again["attempt"]) == (True, 2)
    assert [row["decision_id"] for row in logged(env)] == [first["decision_id"], again["decision_id"]]

    judge = dispatch(env, table, "judge")
    assert (judge["arm"], judge["eligible_arms"]) == ("default", [])

    pinned = dispatch(env, table, "implement", "--job", "job-s1", "--seat", "opus-seat", "--model", "opus-latest",
                      "--effort", "high")
    assert (pinned["arm"], pinned["seat"], pinned["model"], pinned["effort"], pinned["attempt"]) == (
        "explicit", "opus-seat", "claude-opus-5-5", "high", 3)


def test_policy_snapshot_names_one_model_and_effort_per_seat(tmp_path: Path) -> None:
    env = dispatch_env(tmp_path)
    result = route(env, CANONICAL_TABLE, "policy", "snapshot", "--json")
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["policy_rev"] == "2026-10-16.1"
    assert snapshot["background_model"] == "claude-sonnet-5-5"
    assert snapshot["seats"]["planner"] == {"model": "opus-latest", "model_id": "claude-opus-5-5", "effort": "medium"}
    assert snapshot["seats"]["gpt-implementer"]["effort"] == "medium"
    assert snapshot["seats"]["decider"]["model"] == "sonnet-latest"
    assert route(env, CANONICAL_TABLE, "dispatch", "no-such-class").returncode == 2
