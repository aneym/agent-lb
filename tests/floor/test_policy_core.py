from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/coding-agents"
BUDGET = 7168

OBLIGATIONS = {
    "credential floor": r"Never type or print Alex's own passwords, passkeys or codes",
    "outbound exact message": r"No message to a real person without his yes; approval covers that exact message only",
    "spend cap": r"No spend past a stated cap",
    "destructive needs record": r"No destructive data without an approval record on file",
    "secret floor exception": r"Secrets are never printed or written; guards hard-block this",
    "guards block only on floors": r"Guards hard-block only kept floors; everything else warns once",
    "no yes needed list": r"No yes needed: merges, deploys, money path, migrations",
    "relayed yes list": r"His yes, relayed: spend past a cap, destructive data with no record, going public",
    "alex himself": r"Alex himself: his credentials, a message to a real person\.",
    "signed-in clicks need his yes": r"A click in his own signed-in account is allowed with his yes, first-hand or relayed with a time",
    "cos quote qualification": r"CoS message that quotes Alex first-hand with a time, or points at an `alex-\*` approval record, is his yes; never ask him again",
    "decide after scope": r"After a scope is approved, agents decide and log why",
    "mac clicks scope": r"Click macOS consent and TCC prompts, Privacy & Security toggles, system dialogs and app permission prompts yourself",
    "mac clicks log": r"log each with `ledger\.event` and a screenshot",
    "mac clicks exclusions": r"Never his password, Touch ID or codes \(ask for that step only\), no messages, no spend past a cap",
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
    "workflow fan-out": r"Workflows fan out without an agent cap; host admission throttles only at real memory or disk floors\.",
    "slice check": r"Each names its files, fixed interfaces and one check",
}


def installer():
    if str(SOURCE) not in sys.path:
        sys.path.insert(0, str(SOURCE))
    spec = importlib.util.spec_from_file_location("install_policy", SOURCE / "install-policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def without_model_lines(text: str) -> str:
    return re.sub(r"(?m)^(model|effort): .*\n", "", text)


def core_text() -> str:
    return (SOURCE / installer().CORE_SOURCE).read_text()


def test_core_keeps_every_obligation() -> None:
    flat = " ".join(core_text().split())
    missing = [name for name, pattern in OBLIGATIONS.items() if not re.search(pattern, flat)]
    assert not missing, missing
    section = " ".join((SOURCE / "routing/03-who-decides-and-what-goes-to-alex.md").read_text().split())
    assert re.search(
        r"\*\*Clicks in his own signed-in accounts:\*\* allowed with Alex's yes, first-hand or relayed with a time\. "
        r"Never type or read passwords, passkeys or 2FA codes; stop at re-auth\.",
        section,
    )


def test_core_triggers_resolve_to_full_sections() -> None:
    core = core_text()
    assert "`~/.claude/docs/models/README.md`" in core
    assert (SOURCE / "routing/README.md").is_file()
    trigger = next(line for line in core.splitlines() if line.startswith("- **Read first"))
    sections = sorted(set(re.findall(r"`~/\.claude/docs/models/([^`/]+\.md)`", trigger)))
    assert len(sections) >= 5, sections
    unresolved = [name for name in sections if not (SOURCE / "routing" / name).is_file()]
    assert not unresolved, unresolved


def test_core_source_has_no_markers() -> None:
    assert "<!-- core:" not in core_text()


@pytest.fixture
def installed(tmp_path: Path, policy_checkout):
    source = tmp_path / "policy-repo" / "config/coding-agents"
    shutil.copytree(SOURCE, source)
    clients = source.parent.parent / "clients"
    clients.mkdir()
    for path in [ROOT / "clients/route", *sorted((ROOT / "clients").glob("route_*.py"))]:
        shutil.copy2(path, clients / path.name)
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
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
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

    install()
    return SimpleNamespace(home=home, env=env, settings=settings, other=other, install=install)


def test_installed_core_links_resolve_to_installed_sections(installed) -> None:
    models = (installed.home / ".claude/rules/models.md").read_text()
    linked = sorted(set(re.findall(r"`~/(\.claude/docs/models/[^`/]+\.md)`", models)))
    assert len(linked) >= 6, linked
    for relative in linked:
        target = installed.home / relative
        assert target.read_bytes() == (SOURCE / "routing" / target.name).read_bytes(), relative


def test_install_renders_core_within_budget_and_is_idempotent(installed) -> None:
    home, env, settings, other, install = (
        installed.home,
        installed.env,
        installed.settings,
        installed.other,
        installed.install,
    )

    def snapshot() -> dict[str, bytes]:
        return {str(path.relative_to(home)): path.read_bytes() for path in home.rglob("*") if path.is_file()}

    seat = (home / ".claude/agents/codex-verifier-local.md").read_text()
    assert re.search(r"(?m)^model: \S+$", seat) and re.search(r"(?m)^effort: \S+$", seat)
    assert without_model_lines(seat) == without_model_lines((SOURCE / "agents/codex-verifier-local.md").read_text())
    result = subprocess.run(
        [sys.executable, str(home / ".agent-lb/bin/route"), "policy", "snapshot", "--json"],
        env=env | {"ROUTE_TABLE": str(home / ".agent-lb/managed/coding-agents/routing-table.json")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["policy_rev"]
    models = home / ".claude/rules/models.md"
    full = home / ".claude/docs/models-full.md"
    rendered = models.read_text()
    assert len(models.read_bytes()) <= BUDGET, len(models.read_bytes())
    assert rendered.startswith(installer().MODELS_MARKER)
    assert rendered.split("\n\n", 1)[1] == core_text()
    assert not full.exists()
    assert "`~/.claude/docs/models/README.md`" in (home / ".agents/policy/coding-agents/ROUTING.md").read_text()
    installed_settings = json.loads(settings.read_text())
    for key, value in other.items():
        if key != "permissions":
            assert installed_settings[key] == value
    assert set(other["permissions"]["allow"]) <= set(installed_settings["permissions"]["allow"])
    first = snapshot()
    assert "already converged" in install()
    assert snapshot() == first
    install("--uninstall")
    assert not models.exists()
