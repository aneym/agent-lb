# ruff: noqa: E501  (long lines are prompt text the CLI writes verbatim)
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__version__ = "0.1.0"

PKG = Path(__file__).resolve().parent
CATALOG_PATH = PKG / "catalog.json"
TEMPLATES = PKG / "templates"
FACTORY_DIRNAME = ".open-factory"
AGENT_LB_URL = os.environ.get("OPEN_FACTORY_AGENT_LB", "http://127.0.0.1:2455")
# cli.py → open_factory/ → open-factory/ → clients/
REPO_CLIENTS = Path(__file__).resolve().parents[2]
LOCAL_BIN = Path.home() / ".local" / "bin"


def resolve_bin(name: str) -> str | None:
    """Prefer repo clients, then ~/.local/bin, then PATH (avoid zsh function wrappers)."""
    repo = REPO_CLIENTS / name
    if repo.is_file() and os.access(repo, os.X_OK):
        return str(repo)
    local = LOCAL_BIN / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local.resolve() if local.is_symlink() else local)
    return shutil.which(name)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text())


def find_project(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / FACTORY_DIRNAME / "factory.json").is_file():
            return p
    raise SystemExit(f"open-factory: no {FACTORY_DIRNAME}/factory.json above {cur}")


def factory_root(project: Path) -> Path:
    return project / FACTORY_DIRNAME


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


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


def cmd_seats(_: argparse.Namespace) -> int:
    cat = load_catalog()
    seats = cat["seats"]
    print(f"open-factory seats  v{cat.get('version')}  ({len(seats)} seats)\n")
    for s in seats:
        models = " → ".join(s["models"][:4])
        if len(s["models"]) > 4:
            models += " → …"
        agent = s.get("claude_agent") or "—"
        print(f"  {s['id']:<22} {s['role']:<14} agent={agent}")
        print(f"    {s['purpose']}")
        print(f"    models: {models}")
        print()
    print("policy:", json.dumps(cat.get("policy", {}), separators=(",", ":")))
    return 0


def recommend_driver() -> tuple[str, str]:
    """Pick a launcher that won't burn Fable/Astra when those pools are dry.

    Returns (driver, reason).
    """
    code, accounts = http_json(f"{AGENT_LB_URL}/api/accounts")
    if code != 200 or not isinstance(accounts, (list, dict)):
        return "opus", "agent-lb accounts unavailable; default opus"
    rows = accounts if isinstance(accounts, list) else accounts.get("accounts") or accounts.get("data") or []
    fable_ready = False
    opus_ready = False
    openai_ready = False
    for a in rows if isinstance(rows, list) else []:
        if not isinstance(a, dict):
            continue
        prov = str(a.get("provider") or "")
        status = str(a.get("status") or "")
        usage = a.get("usage") or {}
        pri = usage.get("primaryRemainingPercent")
        if prov == "anthropic" and status == "active":
            opus_ready = True
            if a.get("fableEligible") is True and (pri is None or float(pri) > 0):
                fable_ready = True
        if prov == "openai" and status == "active":
            openai_ready = True
    if fable_ready:
        return "fable", "Fable-eligible Anthropic seat active with primary headroom"
    if opus_ready:
        return "opus", "Fable dry/ineligible; use Opus/non-Fable Claude on active seat"
    if openai_ready:
        return "opus", "no Anthropic active; still prefer Claude launcher (OpenAI for seats only)"
    return "opus", "no healthy Anthropic; try opus and expect quota errors"


def cmd_doctor(_: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append((name, ok, detail))

    for bin_name in ("claude", "fable", "cc", "opus", "jev"):
        path = resolve_bin(bin_name)
        add(bin_name, bool(path), path or "not on PATH")

    code, _ = http_json(f"{AGENT_LB_URL}/health/ready")
    if code != 200:
        code, _ = http_json(f"{AGENT_LB_URL}/health")
        add("agent-lb", code == 200, f"{AGENT_LB_URL}/health → {code or 'down'} (ready unavailable)")
    else:
        add("agent-lb", True, f"{AGENT_LB_URL}/health/ready → 200")

    code, accounts = http_json(f"{AGENT_LB_URL}/api/accounts")
    if code == 200 and isinstance(accounts, (list, dict)):
        rows = accounts if isinstance(accounts, list) else accounts.get("accounts") or accounts.get("data") or []
        by_prov: dict[str, list[str]] = {}
        for a in rows if isinstance(rows, list) else []:
            if not isinstance(a, dict):
                continue
            prov = str(a.get("provider") or "?")
            st = str(a.get("status") or "?")
            by_prov.setdefault(prov, []).append(st)
        summary = " · ".join(f"{p}:{','.join(sorted(set(v)))}" for p, v in sorted(by_prov.items()))
        usable = any(s == "active" for vals in by_prov.values() for s in vals)
        add("accounts", usable, summary or "no accounts")
    else:
        add("accounts", False, f"http {code}")

    jev = resolve_bin("jev")
    if jev:
        try:
            p = subprocess.run([jev, "health"], capture_output=True, text=True, timeout=20)
            ok = p.returncode == 0 and "UNAVAILABLE" not in (p.stdout + p.stderr)
            add("jev-health", ok, (p.stdout or p.stderr).strip().splitlines()[-1][:120] if (p.stdout or p.stderr) else f"exit {p.returncode}")
        except Exception as e:
            add("jev-health", False, type(e).__name__)
    else:
        add("jev-health", False, "jev missing")

    herdr = Path.home() / ".herdr" / "worktrees"
    add("herdr-worktrees", herdr.is_dir(), str(herdr))

    catalog_ok = CATALOG_PATH.is_file()
    add("catalog", catalog_ok, str(CATALOG_PATH))

    print(f"open-factory doctor  {utc_now()}\n")
    failed = 0
    for name, ok, detail in checks:
        mark = "ok" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{mark:>4}]  {name:<18} {detail}")
    print()
    driver, reason = recommend_driver()
    print(f"recommended driver: {driver}  ({reason})")
    if driver != "fable":
        print("  tip: open-factory start --driver opus --effort none   # skip Fable + top_thinking")
        print("  tip: hands → GLM/Kimi/OpenRouter Cerebras (Astra secondary is exhausted)")
    if failed:
        print(f"{failed} check(s) failed. Fix before start, or use --force.")
        return 1
    print("ready")
    return 0


def scaffold(project: Path, goal: str, name: str | None) -> Path:
    root = factory_root(project)
    root.mkdir(parents=True, exist_ok=True)
    (root / "dispatch").mkdir(exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    (root / "checks").mkdir(exist_ok=True)

    factory = {
        "name": name or project.name,
        "goal": goal,
        "created_at": utc_now(),
        "catalog_version": load_catalog().get("version"),
        "project_path": str(project),
        "status": "ready",
    }
    (root / "factory.json").write_text(json.dumps(factory, indent=2) + "\n")
    (root / "ledger.jsonl").touch()
    (root / "dispatch" / "log.jsonl").touch()
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

    append_jsonl(
        root / "ledger.jsonl",
        {"ts": utc_now(), "event": "init", "goal": goal, "name": factory["name"]},
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
    print("  factory.json  state.json  ledger.jsonl  dispatch/")
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
    log = root / "dispatch" / "log.jsonl"
    n_disp = sum(1 for _ in log.open()) if log.is_file() else 0
    print(f"project   {project}")
    print(f"name      {factory.get('name')}")
    print(f"goal      {factory.get('goal')}")
    print(f"stage     {state.get('stage')}")
    print(f"dispatch  {n_disp} rows")
    print(f"updated   {state.get('updated_at') or factory.get('created_at')}")
    return 0


def remap_jev(destination: str) -> str:
    cat = load_catalog()
    return cat.get("jev_remap", {}).get(destination, "orchestrator")


def cmd_route(args: argparse.Namespace) -> int:
    prompt = args.prompt
    jev = resolve_bin("jev")
    row: dict[str, Any] = {"ts": utc_now(), "prompt": prompt[:500], "source": "jev"}
    if not jev:
        seat = load_catalog()["policy"].get("fail_open_seat", "implementer-speed")
        row.update({"ok": False, "error": "jev missing", "seat": seat, "fallback": True})
        print(json.dumps(row, indent=2))
        return 0

    try:
        p = subprocess.run(
            [jev, "route", prompt],
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        if p.returncode == 3 or "UNAVAILABLE" in out or "UNAVAILABLE" in err:
            seat = load_catalog()["policy"].get("fail_open_seat", "implementer-speed")
            row.update({"ok": False, "error": "jev unavailable", "seat": seat, "fallback": True})
        else:
            # jev route prints JSON or key lines — try parse
            data: Any = None
            for line in reversed(out.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            if data is None:
                # heuristic: look for destination=
                dest = None
                for line in out.splitlines():
                    if "destination" in line.lower():
                        for part in line.replace(",", " ").split():
                            if part in load_catalog().get("jev_remap", {}):
                                dest = part
                data = {"destination": dest or "opus_subagents", "raw": out[:800]}
            dest = str(data.get("destination") or data.get("dest") or "opus_subagents")
            seat = remap_jev(dest)
            conf = data.get("confidence")
            try:
                conf_f = float(conf) if conf is not None else None
            except (TypeError, ValueError):
                conf_f = None
            if conf_f is not None and conf_f < 0.5:
                seat = load_catalog()["policy"].get("fail_open_seat", "implementer-speed")
                row["low_confidence"] = True
            # jev emits probabilities; only hard-override when clearly high risk
            try:
                risk_f = float(data.get("high_risk") or 0)
            except (TypeError, ValueError):
                risk_f = 1.0 if data.get("high_risk") is True else 0.0
            if risk_f >= 0.7:
                seat = "orchestrator"
                row["high_risk_override"] = True
            # high ambiguity → keep brain involved
            try:
                amb_f = float(data.get("ambiguous") or 0)
            except (TypeError, ValueError):
                amb_f = 0.0
            if amb_f >= 0.75 and seat.startswith("implementer"):
                seat = "planner"
                row["ambiguity_promote"] = True
            row.update({"ok": True, "jev": data, "seat": seat, "destination": dest})
    except Exception as e:
        seat = load_catalog()["policy"].get("fail_open_seat", "implementer-speed")
        row.update({"ok": False, "error": f"{type(e).__name__}: {e}", "seat": seat, "fallback": True})

    # optional project log
    try:
        project = find_project(Path(args.path).resolve() if args.path else None)
        append_jsonl(factory_root(project) / "dispatch" / "log.jsonl", row)
    except SystemExit:
        pass

    print(json.dumps(row, indent=2))
    return 0


def build_orchestrator_prompt(project: Path, goal: str | None) -> str:
    root = factory_root(project)
    orch = (root / "ORCHESTRATOR.md").read_text()
    factory = json.loads((root / "factory.json").read_text())
    seats = load_catalog()["seats"]
    seat_lines = "\n".join(
        f"- `{s['id']}` ({s['role']}): {s['purpose']} · models: {', '.join(s['models'][:3])}"
        for s in seats
    )
    g = goal or factory.get("goal") or ""
    return f"""You are the Open Factory orchestrator for this project.

Project: {project}
Factory: {root}
Goal: {g}

{orch}

## Allowlisted seats (dispatch only these)
{seat_lines}

## Hard rules
1. You are the brain: decide, brief, accept. Do not grind files or run unbounded verifies.
2. One seam → one herdr worktree → one branch → one file set. Claim files before briefing.
3. Independent seats dispatch in one message. Verifier gets a wall-clock budget + named suites.
4. Designer is Fable or Opus only — never Astra/Sol.
5. When Astra is dry: quality hand → Sol Ultrafast (if human waiting) or Opus; speed hand → Cerebras oss/Qwen then Luna/GLM/Kimi.
6. Log every dispatch conceptually; prefer `open-factory route` for new-turn triage.
7. Maker builds; separate checker verifies. Merge-ready is a conjunction of checks.
8. Budget heavy jobs (test/build slots), never refuse agents.
9. While Fable weekly / Astra secondary are dry: orchestrate on Opus with `--effort none` (anthropic_top); hands on GLM/Kimi/Cerebras. Do not start Fable or Astra seats.

Start by reading `{root}/FACTORY.md` and `{root}/state.json`, then propose the first lane plan for the goal.
"""


def cmd_start(args: argparse.Namespace) -> int:
    project = Path(args.path).expanduser().resolve() if args.path else Path.cwd().resolve()
    marker = factory_root(project) / "factory.json"
    if not marker.is_file():
        if args.init_if_missing:
            scaffold(project, goal=args.goal or f"Ship work in {project.name}", name=args.name)
        else:
            raise SystemExit(f"not initialized: {project} — run open-factory init or pass --init-if-missing")

    if not args.force:
        dr = cmd_doctor(argparse.Namespace())
        if dr != 0:
            raise SystemExit("doctor failed; pass --force to start anyway")

    prompt = build_orchestrator_prompt(project, args.goal)
    prompt_path = factory_root(project) / "dispatch" / "last-start-prompt.md"
    prompt_path.write_text(prompt)

    factory = json.loads(marker.read_text())
    if args.goal:
        factory["goal"] = args.goal
    factory["status"] = "running"
    factory["last_start_at"] = utc_now()
    marker.write_text(json.dumps(factory, indent=2) + "\n")
    state_path = factory_root(project) / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    state.update({"stage": "in_execution", "updated_at": utc_now(), "goal": factory["goal"]})
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    append_jsonl(
        factory_root(project) / "ledger.jsonl",
        {"ts": utc_now(), "event": "start", "goal": factory["goal"], "dry_run": bool(args.dry_run)},
    )

    launcher = resolve_bin("fable") or resolve_bin("cc")
    if not launcher:
        raise SystemExit("fable/cc not on PATH")

    repo_clients = REPO_CLIENTS
    driver = (args.driver or "auto").lower()
    if driver == "auto":
        driver, reason = recommend_driver()
        print(f"open-factory auto-driver → {driver} ({reason})")

    if driver == "opus":
        opus_bin = resolve_bin("opus")
        if opus_bin:
            launcher = opus_bin
        else:
            launcher = str(repo_clients / "fable") if (repo_clients / "fable").is_file() else launcher
            os.environ["AGENT_LB_FABLE_MODEL"] = os.environ.get("AGENT_LB_OPUS_MODEL") or "claude-opus-5"
            os.environ["ANTHROPIC_MODEL"] = os.environ["AGENT_LB_FABLE_MODEL"]
    elif driver == "fable":
        print(
            "warning: Fable may be dry; prefer --driver opus until Fable primary returns",
            file=sys.stderr,
        )
        launcher = resolve_bin("fable") or launcher
    elif driver == "cc":
        launcher = resolve_bin("cc") or launcher
    else:
        raise SystemExit(f"unknown --driver {driver!r} (auto|fable|opus|cc)")

    if args.model:
        os.environ["AGENT_LB_FABLE_MODEL"] = args.model
        os.environ["ANTHROPIC_MODEL"] = args.model
        cmd_model = ["--model", args.model]
    else:
        cmd_model = []

    # Default effort: none for non-Fable drivers → anthropic_top (not top_thinking).
    # Claude Code rejects --effort none; claude-lb-launch strips the sentinel.
    effort = (args.effort or "").strip().lower()
    if not effort:
        effort = "high" if driver == "fable" else "none"
    os.environ["CC_EFFORT_LEVEL"] = effort
    cmd_effort = ["--effort", effort] if effort not in {"none", "off"} else ["--effort", "none"]

    cmd = [
        launcher,
        *cmd_model,
        *cmd_effort,
        "--permission-mode",
        args.permission_mode,
        "--append-system-prompt",
        prompt,
    ]
    if args.print:
        cmd.extend(["-p", args.print])
    elif args.goal:
        cmd.append(f"Open Factory start. Goal: {args.goal}. Read .open-factory/FACTORY.md and begin.")

    print("open-factory start")
    print(f"  project   {project}")
    print(f"  driver    {driver}")
    print(f"  launcher  {launcher}")
    print(f"  effort    {effort}")
    print(f"  prompt    {prompt_path}")
    if args.dry_run:
        print("  dry-run   true (not exec)")
        print("  cmdline  ", " ".join(json.dumps(c) if " " in c else c for c in cmd[:8]), "…")
        return 0

    os.chdir(project)
    os.execv(launcher, cmd)


def cmd_report(args: argparse.Namespace) -> int:
    project = find_project(Path(args.path).resolve() if args.path else None)
    root = factory_root(project)
    factory = json.loads((root / "factory.json").read_text())
    log = root / "dispatch" / "log.jsonl"
    rows = []
    if log.is_file():
        for line in log.read_text().splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    seats_used: dict[str, int] = {}
    for r in rows:
        s = str(r.get("seat") or "?")
        seats_used[s] = seats_used.get(s, 0) + 1
    print(f"# Open Factory report — {factory.get('name')}")
    print(f"path: {project}")
    print(f"goal: {factory.get('goal')}")
    print(f"status: {factory.get('status')}")
    print(f"dispatch rows: {len(rows)}")
    if seats_used:
        print("seats:")
        for k, v in sorted(seats_used.items(), key=lambda x: -x[1]):
            print(f"  {k}: {v}")
    print(f"\ncatalog: {CATALOG_PATH}")
    print("doctor tip: open-factory doctor")
    print(f"start tip: open-factory start --path {project}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="open-factory",
        description="Local software factory: Fable orchestrator + allowlisted seats over agent-lb/herdr.",
    )
    p.add_argument("--version", action="version", version=f"open-factory {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seats", help="List allowlisted seats")
    s.set_defaults(func=cmd_seats)

    d = sub.add_parser("doctor", help="Check fable/cc/jev/agent-lb/herdr")
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

    r = sub.add_parser("route", help="Classify a turn via jev → seat id")
    r.add_argument("prompt")
    r.add_argument("--path", default=None)
    r.set_defaults(func=cmd_route)

    start = sub.add_parser("start", help="Launch Fable orchestrator for this project")
    start.add_argument("--path", default=None)
    start.add_argument("--goal", default=None)
    start.add_argument("--name", default=None)
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--force", action="store_true")
    start.add_argument("--init-if-missing", action="store_true")
    start.add_argument("--driver", default="auto", help="auto | fable | opus | cc (auto skips Fable when dry)")
    start.add_argument(
        "--model",
        default=None,
        help="Override driver model (prefer claude-sonnet-5 / claude-opus-5 while Fable thinking is cool)",
    )
    start.add_argument(
        "--effort",
        default=None,
        help="Claude effort (none|low|medium|high). Default: none for opus/cc, high for fable",
    )
    start.add_argument("--permission-mode", default="auto")
    start.add_argument("--print", dest="print", default=None, help="Non-interactive -p prompt")
    start.set_defaults(func=cmd_start)

    rep = sub.add_parser("report", help="Print a short factory report")
    rep.add_argument("--path", default=None)
    rep.set_defaults(func=cmd_report)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
