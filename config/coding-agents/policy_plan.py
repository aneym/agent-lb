from __future__ import annotations  # noqa: I001

import json
import os
from pathlib import Path

from policy_documents import read_text  # type: ignore[missing-import]
from policy_files import desired_routing_table  # type: ignore[missing-import]
from policy_hook_groups import installed_manifest  # type: ignore[missing-import]
from policy_layout import ADOPTED_KEEP, DISPATCH_SCRIPT, HOOK_MANIFEST, MANAGED_AGENTS, MANAGED_DIR, POLICY_DIR, POLICY_FILES, POLICY_SNAPSHOT, RETIRED_AGENTS, RETIRED_HOOKS, ROUTE_FILES, ROUTING_TABLE, ROUTING_TABLE_OWNER  # type: ignore[missing-import]  # noqa: I001  # fmt: skip
from policy_settings import seat_template  # type: ignore[missing-import]


def plan_managed_files(args, source, snapshot, keep_fold, changes):
    preserved_agents: list[tuple[str, Path]] = []
    replace_policy_link = False
    policy_link_target = ""
    for relative_path, owner_relative_path, owner_marker, template_relative_path in MANAGED_AGENTS:
        agent_path = args.home / relative_path
        owner_path = args.home / owner_relative_path
        agent_template = (source / template_relative_path).read_text()
        if args.uninstall and (args.home / POLICY_SNAPSHOT).is_file():
            installed_snapshot = json.loads((args.home / POLICY_SNAPSHOT).read_text())
            if template_relative_path.parent == Path("agents"):
                agent_template = seat_template(agent_template, template_relative_path.stem, installed_snapshot)
        if snapshot is not None and template_relative_path.parent == Path("agents"):
            agent_template = seat_template(agent_template, template_relative_path.stem, snapshot)
        agent_text = read_text(agent_path)
        agent_owned = read_text(owner_path) == owner_marker
        if args.uninstall:
            if agent_owned and relative_path in ADOPTED_KEEP:
                changes[owner_path] = None
                if agent_path.exists():
                    preserved_agents.append(("adopted guard, still registered,", agent_path))
            elif agent_owned:
                changes[owner_path] = None
                if agent_text == agent_template:
                    changes[agent_path] = None
                elif agent_path.exists():
                    preserved_agents.append(("customized", agent_path))
            elif agent_path.exists():
                preserved_agents.append(("unmanaged", agent_path))
        else:
            if keep_fold and template_relative_path in (Path(DISPATCH_SCRIPT), Path("hooks/hook-dispatch-parity.py")):
                if agent_text != agent_template:
                    print(
                        f"hook dispatcher: keeping the installed {relative_path.name}; the new one failed the fixture"
                    )
                continue
            if agent_text != agent_template:
                changes[agent_path] = agent_template
            if not agent_owned:
                changes[owner_path] = owner_marker

    if args.uninstall:
        if (args.home / POLICY_SNAPSHOT).exists():
            changes[args.home / POLICY_SNAPSHOT] = None
    else:
        wanted_snapshot = json.dumps(snapshot, indent=2) + "\n"
        if read_text(args.home / POLICY_SNAPSHOT) != wanted_snapshot:
            changes[args.home / POLICY_SNAPSHOT] = wanted_snapshot

        for name, template in (
            ("route.sh", "jev-route.sh"),
            ("health-check.sh", "jev-health-check.sh"),
            ("jev-health.py", "jev-health.py"),
        ):
            target = args.home / ".jev/hooks" / name
            wanted = (source / "hooks" / template).read_text()
            if read_text(target) != wanted or (target.suffix == ".sh" and target.stat().st_mode & 0o777 != 0o755):
                changes[target] = wanted
        luna_owner = args.home / ".agent-lb/managed/coding-agents/luna-implementer"
        if read_text(luna_owner) == "agent-lb:luna-implementer:v1\n":
            for relative in (
                Path(".claude/agents/luna-implementer.md"),
                Path(".agent-lb/managed/coding-agents/luna-implementer"),
                POLICY_DIR / "agents/luna-implementer.md",
            ):
                path = args.home / relative
                if path.is_file() and path.resolve().parent != source / "agents":
                    changes[path] = None
        for name in RETIRED_HOOKS:
            stub = args.home / ".claude/hooks" / name
            if read_text(stub) != "import sys; sys.stdin.read()\n":
                changes[stub] = "import sys; sys.stdin.read()\n"
            for relative in (POLICY_DIR / "hooks" / name, MANAGED_DIR / Path(name).stem):
                target = args.home / relative
                if target.is_file() and not target.is_symlink():
                    changes[target] = None
        for name in RETIRED_AGENTS:
            for relative in (
                Path(".claude/agents") / f"{name}.md",
                MANAGED_DIR / name,
                POLICY_DIR / "agents" / f"{name}.md",
            ):
                if (args.home / relative).is_file() and (args.home / relative).resolve().parent != source / "agents":
                    changes[args.home / relative] = None
        table_path = args.home / ROUTING_TABLE
        desired_table = desired_routing_table(source / "routing-table.json", table_path)
        if desired_table != read_text(table_path):
            changes[table_path] = desired_table
        owner_path, owner_marker = ROUTING_TABLE_OWNER
        if read_text(args.home / owner_path) != owner_marker:
            changes[args.home / owner_path] = owner_marker
        policy_dir = args.home / POLICY_DIR

        root = source.parents[1]
        for client in [*(root / "clients" / n for n in ROUTE_FILES), root / "scripts/llm-usage-publish.sh"]:
            target = args.home / ".agent-lb/bin" / client.name
            if client.is_file() and read_text(target) != (text := client.read_text()):
                changes[target] = text
        route_owner = args.home / ROUTING_TABLE.with_name("route-cli")
        if read_text(route_owner) != (marker := "agent-lb:route-cli:v1\n"):
            changes[route_owner] = marker
        target = args.home / ROUTING_TABLE.with_name("quota-swap.json")
        wanted = (source / "quota-swap.json").read_text()
        if read_text(target) != wanted:
            changes[target] = wanted
        decider_source = source.parent.parent / "clients" / "open-factory" / "open_factory" / "decider.json"
        if decider_source.is_file():
            decider_path = args.home / ROUTING_TABLE.with_name("decider.json")
            if read_text(decider_path) != decider_source.read_text():
                changes[decider_path] = decider_source.read_text()

        replace_policy_link = policy_dir.is_symlink()
        if replace_policy_link:
            policy_link_target = os.readlink(policy_dir)
        if replace_policy_link or policy_dir.resolve() != source:
            policy_sources = [Path(name) for name in POLICY_FILES]
            policy_sources += [path.relative_to(source) for path in sorted((source / "routing").glob("*.md"))]
            policy_sources += [path.relative_to(source) for path in sorted((source / "agents").glob("*.md"))]

            policy_sources += [
                path.relative_to(source)
                for pattern in ("*.py", "*.sh")
                for path in sorted((source / "hooks").glob(pattern))
            ]
            policy_sources += [
                Path("bin/kimi-seat"),
                Path("bin/openrouter-seat"),
                Path("bin/seat_launcher.py"),
                Path("hooks/local-seat-bin/desktop-guard-focus"),
            ]
            policy_sources += [Path(HOOK_MANIFEST)]
            for relative in policy_sources:
                wanted = (source / relative).read_text()
                if relative.parent == Path("agents"):
                    wanted = seat_template(wanted, relative.stem, snapshot)
                elif relative == Path(HOOK_MANIFEST):
                    wanted = installed_manifest(source)

                if replace_policy_link or read_text(policy_dir / relative) != wanted:
                    changes[policy_dir / relative] = wanted

    return preserved_agents, replace_policy_link, policy_link_target
