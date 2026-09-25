# ruff: noqa: E501  (long lines are prompt text the CLI writes verbatim)
from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from . import aa
from .common import PKG, resolve_bin, utc_now
from .decide import DECIDERS, decide, ledger_path, load_policy, read_decisions, run_route

__version__ = "0.2.0"

TEMPLATES = PKG / "templates"
FACTORY_DIRNAME = ".open-factory"
AGENT_LB_URL = os.environ.get("OPEN_FACTORY_AGENT_LB", "http://127.0.0.1:2455")
CLAUDE_LAUNCHER = "claude-lb-launch"
# The `cc` / `opus` shell functions: Opus drives with the 1M window, compacting near 300k.
DRIVER_ARGS = ["--dangerously-skip-permissions", "--model", "opus[1m]", "--effort", "high", "--autocompact", "300k"]
WORK_CLASSES = ("explore", "research", "implement", "mechanical", "review", "verify", "plan")


def find_project(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / FACTORY_DIRNAME / "factory.json").is_file():
            return p
    raise SystemExit(f"open-factory: no {FACTORY_DIRNAME}/factory.json above {cur}")


def factory_root(project: Path) -> Path:
    return project / FACTORY_DIRNAME


def http_json(url: str, timeout: float = 5.0) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


def scaffold(project: Path, goal: str, name: str | None) -> Path:
    root = factory_root(project)
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    (root / "checks").mkdir(exist_ok=True)

    factory = {
        "name": name or project.name,
        "goal": goal,
        "created_at": utc_now(),
        "decider_policy_version": load_policy().get("version"),
        "project_path": str(project),
        "status": "ready",
    }
    (root / "factory.json").write_text(json.dumps(factory, indent=2) + "\n")
    (root / "state.json").write_text(
        json.dumps(
            {
                "stage": "ready",
                "goal": goal,
                "updated_at": utc_now(),
                "held_for_alex": [],
                "active_lanes": [],
            },
            indent=2,
        )
        + "\n"
    )

    for tmpl in ("FACTORY.md", "ORCHESTRATOR.md", "CLAUDE.factory.md"):
        src = TEMPLATES / tmpl
        dst = root / tmpl
        text = src.read_text()
        text = text.replace("{{NAME}}", factory["name"])
        text = text.replace("{{GOAL}}", goal)
        text = text.replace("{{PATH}}", str(project))
        text = text.replace("{{DATE}}", utc_now()[:10])
        dst.write_text(text)

    # Pointer in repo CLAUDE.md fragment (append-only marker file)
    pointer = project / "OPEN_FACTORY.md"
    if not pointer.exists():
        pointer.write_text(
            f"# Open Factory\n\nProject factory state lives in `{FACTORY_DIRNAME}/`.\n\n"
            f"Start: `open-factory start`\nDoctor: `open-factory doctor`\n"
            f"Goal: {goal}\n"
        )

    return root


def cmd_init(args: argparse.Namespace) -> int:
    project = Path(args.path).expanduser().resolve() if args.path else Path.cwd().resolve()
    if not project.is_dir():
        raise SystemExit(f"open-factory init: not a directory: {project}")
    existing = factory_root(project) / "factory.json"
    if existing.is_file() and not args.force:
        raise SystemExit(f"already initialized: {existing} (use --force to rewrite)")
    goal = args.goal or f"Ship work in {project.name}"
    root = scaffold(project, goal=goal, name=args.name)
    print(f"initialized {root}")
    print(f"  factory.json  state.json  (decisions go to {ledger_path()})")
    print(f"  start with: open-factory start --path {project}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project = Path(args.path).expanduser().resolve() if args.path else None
    try:
        project = find_project(project)
    except SystemExit:
        if project is None:
            raise
        raise SystemExit(f"open-factory: not initialized at {project} — run open-factory init")
    root = factory_root(project)
    factory = json.loads((root / "factory.json").read_text())
    state = json.loads((root / "state.json").read_text()) if (root / "state.json").is_file() else {}
    n_disp = len(read_decisions(project))
    print(f"project   {project}")
    print(f"name      {factory.get('name')}")
    print(f"goal      {factory.get('goal')}")
    print(f"stage     {state.get('stage')}")
    print(f"decisions {n_disp} (of_decision rows in {ledger_path()})")
    print(f"updated   {state.get('updated_at') or factory.get('created_at')}")
    return 0


def cmd_seats(args: argparse.Namespace) -> int:
    route = resolve_bin("route")
    if not route:
        raise SystemExit("open-factory: route not found")
    extra = ["--class", args.task_class] if args.task_class else []
    return subprocess.run([route, "menu", *extra, *(["--json"] if args.json else [])], check=False).returncode


def cmd_doctor(_: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    for name in (CLAUDE_LAUNCHER, "route", "jev"):
        path = resolve_bin(name)
        checks.append((name, bool(path), path or "not found"))
    code, _ = http_json(f"{AGENT_LB_URL}/health")
    checks.append(("agent-lb", code == 200, f"{AGENT_LB_URL}/health -> {code or 'down'}"))
    code, menu, err = run_route("menu", "--json")
    if isinstance(menu, dict):
        runnable = sorted({seat["id"] for entry in menu["classes"].values() for seat in entry.get("seats", [])})
        checks.append(("menu", bool(runnable), f"{len(runnable)} runnable seats (pools: {menu.get('poolsSource')})"))
    else:
        checks.append(("menu", False, err or f"route menu exit {code}"))
    jev = resolve_bin("jev")
    if jev:
        proc = subprocess.run([jev, "health"], capture_output=True, text=True, timeout=20, check=False)
        text = (proc.stdout or proc.stderr).strip()
        checks.append(
            (
                "jev-health",
                proc.returncode == 0 and "UNAVAILABLE" not in text,
                text.splitlines()[0][:100] if text else f"exit {proc.returncode}",
            )
        )

    print(f"open-factory doctor  {utc_now()}\n")
    failed = 0
    for name, ok, detail in checks:
        failed += not ok
        print(f"  [{'ok' if ok else 'FAIL':>4}]  {name:<18} {detail}")
    print()
    if failed:
        print(f"{failed} check(s) failed. Fix before start, or use --force.")
        return 1
    print("ready")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    receipt = decide(args.task, args.task_class, decider=args.decider, context=args.context)
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0
    how = (
        f"picked by {args.decider}"
        if receipt["validation"] == "accepted"
        else f"fallback {receipt['fallback']} ({receipt['abstain'] or receipt['validation']})"
    )
    print(f"seat      {receipt['seat']}")
    print(f"model     {receipt['model'] or '-'}")
    print(f"pool      {receipt['pool'] or '-'}")
    print(f"decision  {how}; {receipt['total_ms']} ms; id {receipt['decision_id']}")
    return 0


def menu_text(menu: dict[str, Any]) -> str:
    lines = []
    for name in WORK_CLASSES:
        entry = menu.get("classes", {}).get(name)
        if not entry:
            continue
        seats = (
            ", ".join(f"`{seat['seat']}` on `{seat['model']}`" for seat in entry.get("seats", []))
            or "nothing routable; stay on the driver"
        )
        lines.append(f"- {name}: {seats}")
    return "\n".join(lines)


def build_orchestrator_prompt(project: Path, goal: str | None) -> str:
    root = factory_root(project)
    orch = (root / "ORCHESTRATOR.md").read_text()
    factory = json.loads((root / "factory.json").read_text())
    try:
        menu = menu_text(fetch_menu_all())
    except SystemExit as error:
        menu = f"(route menu unavailable: {error})"
    return f"""You are the Open Factory orchestrator for this project.

Project: {project}
Factory: {root}
Goal: {goal or factory.get("goal") or ""}

{orch}

## Routing (per call, not per session)

Before each subagent or Workflow agent, get the seat from the host router:

    open-factory route --class <explore|research|implement|mechanical|review|verify|plan> "<one-paragraph task>"

It returns a seat and model and records the decision in the dispatch ledger. Dispatch to
exactly that seat: Claude models as `Agent`/`agent()` with that `model`; GPT seats as `gpt-implementer`
(sol-latest), `luna-implementer` (luna-latest), `gpt-explorer` or `codex-sol`; Cursor and
Devin through `cursor-seat` and `devin-seat`. If it returns `driver`, do the work in this
session. Never pick a seat the router did not return.

Seats runnable at launch ({utc_now()}; the router re-checks live):
{menu}

## Rules
1. Decide, brief, accept. Seats do the volume work.
2. One seam, one worktree, one branch, one file set.
3. Independent seats go out in one message. Verify by running the work.
"""


def fetch_menu_all() -> dict[str, Any]:
    code, menu, err = run_route("menu", "--json")
    if not isinstance(menu, dict):
        raise SystemExit(err or f"route menu exit {code}")
    return menu


def cmd_start(args: argparse.Namespace) -> int:
    project = Path(args.path).expanduser().resolve() if args.path else Path.cwd().resolve()
    marker = factory_root(project) / "factory.json"
    if not marker.is_file():
        if not args.init_if_missing:
            raise SystemExit(f"not initialized: {project}; run open-factory init or pass --init-if-missing")
        scaffold(project, goal=args.goal or f"Ship work in {project.name}", name=args.name)
    if not args.force and cmd_doctor(argparse.Namespace()) != 0:
        raise SystemExit("doctor failed; pass --force to start anyway")

    prompt = build_orchestrator_prompt(project, args.goal)
    prompt_path = factory_root(project) / "last-start-prompt.md"
    prompt_path.write_text(prompt)
    factory = json.loads(marker.read_text())
    if args.goal:
        factory["goal"] = args.goal
    factory.update(status="running", last_start_at=utc_now())
    marker.write_text(json.dumps(factory, indent=2) + "\n")

    launcher = resolve_bin(CLAUDE_LAUNCHER)
    if not launcher:
        raise SystemExit(f"{CLAUDE_LAUNCHER} not found")
    cmd = [launcher, *DRIVER_ARGS, "--append-system-prompt", prompt]
    if args.print:
        cmd.extend(["-p", args.print])
    elif args.goal:
        cmd.append(f"Open Factory start. Goal: {args.goal}. Read .open-factory/FACTORY.md and begin.")
    print(f"open-factory start\n  project   {project}\n  launcher  {launcher} (Opus drives)\n  prompt    {prompt_path}")
    if args.dry_run:
        print("  dry-run   true (not exec)")
        return 0
    os.chdir(project)
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION"}}
    os.execve(launcher, cmd, env)


def cmd_report(args: argparse.Namespace) -> int:
    project = Path(args.path).expanduser().resolve() if args.path else None
    rows = read_decisions(project)
    print(f"# Open Factory decisions{f' under {project}' if project else ''}: {len(rows)}")
    if not rows:
        return 0
    accepted = sum(r.get("validation") == "accepted" for r in rows)
    print(
        f"accepted picks {accepted}/{len(rows)}; fallbacks {Counter(r.get('fallback') for r in rows if r.get('fallback'))}"
    )
    print(f"abstain reasons {Counter(r.get('abstain') for r in rows if r.get('abstain'))}")
    print("seats:")
    for (seat, model), n in Counter((r.get("seat"), r.get("model")) for r in rows).most_common():
        print(f"  {seat}/{model}: {n}")
    ms = sorted(r["decider_ms"] for r in rows if isinstance(r.get("decider_ms"), int))
    if ms:
        print(f"decider latency p50 {ms[len(ms) // 2]} ms, max {ms[-1]} ms")
    return 0


def cmd_aa_sync(args: argparse.Namespace) -> int:
    try:
        result = aa.sync(dry_run=args.dry_run, fixture=Path(args.fixture) if args.fixture else None, force=args.force)
    except (OSError, ValueError) as error:
        # Only locally defined errors are safe to print; network exceptions are sanitized in aa.fetch_models.
        raise SystemExit(f"open-factory aa-sync: {error}") from None
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print(f"snapshot  {result['snapshot_path']}")
    age = result["age_seconds"]
    print(f"age       {age / 3600:.1f}h" if age is not None else "age       dry run: fixture numbers, not AA data")
    if result["skipped"]:
        print("fetch     skipped (snapshot under 24h old)")
    print("alias         variant   intelligence  coding  $/1M  tokens/s  slug")
    for alias in aa._mapping():
        for row in result[alias]:
            print(
                f"{alias:<13} {row['variant']:<9} {str(row['intelligence_index']):<13} "
                f"{str(row['coding_index']):<7} {str(row['price_blended_usd_per_1m']):<5} "
                f"{str(row['output_tokens_per_s']):<9} {row['slug']}"
            )
    print(f"unmapped  {len(result['unmapped'])} AA models (listed with --json)")
    print("source    Artificial Analysis (https://artificialanalysis.ai/)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="open-factory",
        description="Open Factory: an Opus host that routes each call across subscriptions through agent-lb.",
    )
    p.add_argument("--version", action="version", version=f"open-factory {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seats", help="Seats runnable now, per class (route menu)")
    s.add_argument("--class", dest="task_class", default=None)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_seats)

    d = sub.add_parser("doctor", help="Check the launcher, route, jev and agent-lb")
    d.set_defaults(func=cmd_doctor)

    i = sub.add_parser("init", help="Scaffold .open-factory in a project")
    i.add_argument("path", nargs="?", default=None)
    i.add_argument("--goal", default=None)
    i.add_argument("--name", default=None)
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    st = sub.add_parser("status", help="Show factory status")
    st.add_argument("--path", default=None)
    st.set_defaults(func=cmd_status)

    r = sub.add_parser("route", help="Pick the seat for one task: menu, decider, host re-check, receipt")
    r.add_argument("task")
    r.add_argument("--class", dest="task_class", default="implement", choices=WORK_CLASSES)
    r.add_argument("--decider", default=None, choices=DECIDERS, help="default from decider.json")
    r.add_argument("--context", default=None, help="extra state for the decider")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_route)

    start = sub.add_parser("start", help="Launch the Opus orchestrator (cc) for this project")
    start.add_argument("--path", default=None)
    start.add_argument("--goal", default=None)
    start.add_argument("--name", default=None)
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--force", action="store_true")
    start.add_argument("--init-if-missing", action="store_true")
    start.add_argument("--print", dest="print", default=None, help="Non-interactive -p prompt")
    start.set_defaults(func=cmd_start)

    aa_sync = sub.add_parser("aa-sync", help="Fetch Artificial Analysis benchmarks as routing evidence")
    aa_sync.add_argument("--force", action="store_true", help="fetch even if the snapshot is under 24h old")
    aa_sync.add_argument("--dry-run", action="store_true", help="read a fixture without a key or disk writes")
    aa_sync.add_argument("--fixture", default=None, help="fixture path for --dry-run")
    aa_sync.add_argument("--json", action="store_true")
    aa_sync.set_defaults(func=cmd_aa_sync)

    rep = sub.add_parser("report", help="Summarize routing decisions from the dispatch ledger")
    rep.add_argument("--path", default=None, help="only decisions made under this directory")
    rep.set_defaults(func=cmd_report)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "decider", "x") is None:
        args.decider = load_policy().get("decider", "jev")
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
