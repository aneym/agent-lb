from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/coding-agents"
BUDGET = 6144

OBLIGATIONS = {
    "credential floor": r"Never type or print Alex's own passwords, passkeys or codes",
    "outbound exact message": r"No message to a real person without his yes; approval covers that exact message only",
    "spend cap": r"No spend past a stated cap",
    "destructive needs record": r"No destructive data without an approval record on file",
    "secret floor exception": r"secret floor is off until launch, never for his own credentials",
    "guards block only on floors": r"A guard blocks only on a kept floor and names it",
    "no yes needed list": r"No yes needed: merges, deploys, money path, migrations",
    "relayed yes list": r"His yes, relayed: spend past a cap, destructive data with no record, going public",
    "alex himself": r"Alex himself: his credentials, a click in his own signed-in account, a message to a real person",
    "cos quote qualification": r"CoS message that quotes Alex first-hand with a time, or points at an `alex-\*` approval record, is his yes; never ask him again",
    "decide after scope": r"After a scope is approved, agents decide and log why",
    "mac clicks scope": r"Click macOS consent and TCC prompts, Privacy & Security toggles, system dialogs and app permission prompts yourself",
    "mac clicks log": r"log each with `ledger\.event` and a screenshot",
    "mac clicks exclusions": r"Never his password, Touch ID or codes \(ask for that step only\), no messages, no spend past a cap",
    "no code comments": r"No code comments \(2026-10-14\)\*\* in any repo the factory works in",
    "comment exceptions": r"Keep only shebangs, license headers, compiler/lint/type directives, generated-file markers",
    "opus architects": r"Opus \(opus-latest medium\) owns architecture, contracts, specs, plans, verdicts and UI design",
    "top level medium": r"never xhigh, max or Fable by default",
    "sol only to spec": r"Sol implements only to an Opus spec and writes no scopes, specs, contracts, docs, research synthesis or rulings",
    "opus seat for trust code": r"UI-critical, contract and trust-boundary code: opus-seat",
    "astra only": r"Architecture review is `astra-consult` only, never `sol-consult`",
    "seat table implement": r"Code to a spec: gpt-implementer sol-latest medium",
    "seat table review": r"verifier sonnet-latest high for non-Claude authors, codex-verifier sol-latest high for Claude authors",
    "grok never reviews": r"Grok and Composer never review",
    "precedence explicit": r"explicit flags win; constraints only narrow",
    "precedence reserve": r"under the reserve waits \(exit 3\), never another model",
    "precedence sticky": r"a job keeps its first arm",
    "never explore list": r"exploration never touches plan, review, spec, contract, verdict, judge, design, audit, UI-critical, trust-boundary, money-path or fix work",
    "hook cap": r"at most 1024 UTF-8 bytes per hook",
    "270 s": r"No tool call blocks past 270 s",
    "no watchers": r"No watchers or gh poll loops",
    "factory-wait": r"`factory-wait --repo <repo> --sha <sha> --until <state> --max 270`",
    "rails merge path": r"merges only with `scripts/studio_merge\.py merge <branch>` on the main machine; never `gh pr merge`",
    "conflict rebase once": r"on a conflict rebase once, then hand to the lane lead",
    "other repos ff": r"Other repos land by fast-forward push to `origin/main`",
    "check evidence": r"harness check record or a commit-body `check:` line",
    "review on request": r"Review seats run only when a lead names one and logs why",
    "fix forward": r"fixed forward as the next slice",
    "agent teams": r"Agent teams are the default",
    "taskstop": r"Stand down an infra action with TaskStop",
    "box placement default": r"Work that is not Mac-only runs on the box `seat-run` picks",
    "forge factory only": r"Forge runs factory work only, never personal agents",
    "computer use off desktop": r"Computer use never runs on Alex's desktop",
    "plan docs": r"Docs Alex reads \(2026-10-15\)\*\* are Agent Native Plan docs via `plan-publish`",
    "workflow cap": r"at most 6 agents in tight mode or 16 normal",
    "slice check": r"Each names its files, fixed interfaces and one check",
}


def installer():
    spec = importlib.util.spec_from_file_location("install_policy", SOURCE / "install-policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def core_and_full() -> tuple[str, str]:
    core, full = installer().split_core((SOURCE / "ROUTING.md").read_text())
    assert core is not None, "ROUTING.md has no core block"
    return core, full


def test_core_keeps_every_obligation() -> None:
    core, _ = core_and_full()
    flat = " ".join(core.split())
    missing = [name for name, pattern in OBLIGATIONS.items() if not re.search(pattern, flat)]
    assert not missing, missing


def test_core_triggers_resolve_to_full_sections() -> None:
    core, full = core_and_full()
    assert "`~/.claude/docs/models-full.md`" in core
    trigger = next(line for line in core.splitlines() if line.startswith("- **Read first.**"))
    sections = re.findall(r'"([^"]+)"', trigger)
    assert len(sections) >= 5
    headings = [line[3:] for line in full.splitlines() if line.startswith("## ")]
    unresolved = [name for name in sections if not any(heading.startswith(name + " (") for heading in headings)]
    assert not unresolved, unresolved


def test_full_text_is_routing_without_markers() -> None:
    text = (SOURCE / "ROUTING.md").read_text()
    _, full = core_and_full()
    assert full == "".join(line for line in text.splitlines(keepends=True) if "<!-- core:" not in line)
    assert "<!-- core:" not in full


@pytest.mark.parametrize(
    "text, error",
    [
        ("<!-- core:begin a -->\nx\n", "never closed"),
        ("<!-- core:begin a -->\n<!-- core:begin b -->\n<!-- core:end b -->\n<!-- core:end a -->\n", "opens inside"),
        ("<!-- core:begin a -->\n<!-- core:end a -->\n<!-- core:begin a -->\n<!-- core:end a -->\n", "not unique"),
        ("<!-- core:end a -->\n", "does not close"),
        ("<!-- core:begin A -->\n", "malformed"),
    ],
)
def test_invalid_markers_are_refused(text: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        installer().split_core(text)


def test_install_renders_core_within_budget_and_is_idempotent(tmp_path: Path, policy_checkout) -> None:
    source = tmp_path / "policy-repo" / "config/coding-agents"
    shutil.copytree(SOURCE, source)
    (source.parent.parent / "clients").mkdir()
    for name in ("route", "route_quota.py", "route_storage.py", "route_cache.py"):
        shutil.copy2(ROOT / "clients" / name, source.parent.parent / "clients" / name)
    policy_checkout(tmp_path / "policy-repo")
    home = tmp_path / "home"
    settings = home / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    other = {"theme": "dark", "permissions": {"allow": ["Bash(ls:*)"]}, "customKey": [1, 2, {"x": "y"}]}
    settings.write_text(json.dumps(other, indent=2) + "\n")
    env = os.environ | {
        "HOME": str(home),
        "AGENT_LB_URL": "http://127.0.0.1:1",
        "ROUTE_MODELS_CACHE": str(tmp_path / "models-cache.json"),
    }
    for key in ("ROUTE_FIXTURE_DIR", "ROUTE_TABLE"):
        env.pop(key, None)

    def install(*options: str) -> str:
        return subprocess.run(
            [sys.executable, str(source / "install-policy.py"), "--home", str(home), *options],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    def snapshot() -> dict[str, bytes]:
        return {str(path.relative_to(home)): path.read_bytes() for path in home.rglob("*") if path.is_file()}

    install()
    assert (home / ".claude/agents/codex-verifier-local.md").read_bytes() == (SOURCE / "agents/codex-verifier-local.md").read_bytes()
    result = subprocess.run(
        [sys.executable, str(home / ".agent-lb/bin/route"), "policy", "snapshot", "--json"],
        env=env | {"ROUTE_TABLE": str(home / ".agent-lb/managed/coding-agents/routing-table.json")},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["policy_rev"]
    models = home / ".claude/rules/models.md"
    full = home / ".claude/docs/models-full.md"
    policy = home / ".agents/policy/coding-agents/ROUTING.md"
    core, full_text = core_and_full()
    assert len(models.read_bytes()) <= BUDGET, len(models.read_bytes())
    assert models.read_text().endswith("\n\n" + core)
    assert full.read_text().endswith("\n\n" + full_text)
    assert policy.read_bytes() == (SOURCE / "ROUTING.md").read_bytes()
    installed = json.loads(settings.read_text())
    for key, value in other.items():
        if key != "permissions":
            assert installed[key] == value
    assert set(other["permissions"]["allow"]) <= set(installed["permissions"]["allow"])
    first = snapshot()
    assert "already converged" in install()
    assert snapshot() == first
    install("--uninstall")
    assert not models.exists() and not full.exists()
