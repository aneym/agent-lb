import fnmatch
import json
import re


def verify_installation(
    SOURCE_ONLY,
    HOME,
    SOURCE,
    CANONICAL,
    check,
    read,
    unpinned,
    frontmatter_model,
    forbidden,
    readmitted,
    MARKER_START,
    MARKER_END,
    MANAGED,
):
    settings = {}
    if not SOURCE_ONLY:
        for name in ("claude-adapter.md", "routing-table.json"):
            check(read(CANONICAL / name) == read(SOURCE / name) != "", f"{CANONICAL / name} matches {SOURCE / name}")

        for section in sorted((SOURCE / "routing").glob("*.md")):
            check(
                read(HOME / ".claude/docs/models" / section.name) == read(section),
                f"installed routing section matches {section.name}",
            )
        claude_doc = HOME / ".claude" / "CLAUDE.md"
        claude_text = read(claude_doc)
        check(
            claude_text.count(MARKER_START) == 1 and claude_text.count(MARKER_END) == 1,
            f"{claude_doc} has exactly one managed routing block",
        )
        check(
            read(SOURCE / "claude-adapter.md").strip() in claude_text, f"{claude_doc} routing block matches the adapter"
        )
        check(
            MARKER_START not in read(HOME / ".codex" / "AGENTS.md"),
            "~/.codex/AGENTS.md carries no managed routing block",
        )

        settings_path = HOME / ".claude" / "settings.json"
        try:
            settings = json.loads(read(settings_path) or "{}")
        except json.JSONDecodeError:
            settings = {}
            check(False, f"{settings_path} contains valid JSON")
        check(settings.get("model") == "opus", "direct Claude Code default model is opus (Opus 5.5)")
        check(settings.get("effortLevel") == "medium", "direct Claude Code default effort is medium")
        sonnet_pin = (
            (settings.get("env") or {}).get("ANTHROPIC_DEFAULT_SONNET_MODEL")
            if isinstance(settings.get("env"), dict)
            else None
        )
        check(
            isinstance(sonnet_pin, str) and re.fullmatch(r"claude-sonnet-\d+(?:-\d+)?", sonnet_pin) is not None,
            f"settings.json pins Claude Code's sonnet alias (ANTHROPIC_DEFAULT_SONNET_MODEL={sonnet_pin})",
        )

        for name, installed, source in MANAGED:
            check(
                read(SOURCE / source) != "" and unpinned(read(HOME / installed)) == unpinned(read(SOURCE / source)),
                f"{installed} matches {source} apart from snapshot-resolved model and effort",
            )
            owner = HOME / ".agent-lb" / "managed" / "coding-agents" / name
            check(read(owner) == f"agent-lb:{name}:v1\n", f"{owner} records installer ownership")

        for retired_seat in ("astra", "implementer"):
            installed = (HOME / ".claude" / "agents" / f"{retired_seat}.md").exists()
            check(not installed, f"the retired {retired_seat} seat is not installed")

        for path in sorted((HOME / ".claude" / "agents").glob("*.md")):
            model = frontmatter_model(read(path)) or ""
            allowed = any(fnmatch.fnmatchcase(model.lower(), pattern) for pattern in readmitted(path.stem))
            check(not forbidden(model) or allowed, f"{path.name} does not pin a Fable model ({model or 'unset'})")
    return settings
