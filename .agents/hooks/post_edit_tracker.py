#!/usr/bin/env python3


import json
import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    from _analytics import emit_event
except ImportError:

    def emit_event(*_a: object, **_k: object) -> None:
        pass



CODE_EXTENSIONS = {".py"}


CODE_DIRS = {"app/", "tests/"}


EXCLUDE_PATTERNS = {"__pycache__/", ".claude/", ".agents/"}


def is_code_file(file_path: str, project_dir: str) -> bool:

    if Path(file_path).suffix not in CODE_EXTENSIONS:
        return False

    try:
        rel = str(Path(file_path).relative_to(project_dir))
    except ValueError:
        return False

    if any(excl in rel for excl in EXCLUDE_PATTERNS):
        return False

    return any(rel.startswith(d) for d in CODE_DIRS)


def main() -> None:
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    session_id = input_data.get("session_id", "unknown")
    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    project_dir = input_data.get("cwd", "")

    if not file_path or not is_code_file(file_path, project_dir):
        sys.exit(0)


    hook_dir = Path(__file__).resolve().parent
    state_dir = hook_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"dirty-{session_id}.json"


    state: dict[str, object] = {"modified": True, "files": [], "last_modified": ""}
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (json.JSONDecodeError, PermissionError):
            pass


    files = state.get("files", [])
    if isinstance(files, list) and file_path not in files:
        files.append(file_path)
    state["files"] = files
    state["modified"] = True
    state["last_modified"] = datetime.now(UTC).isoformat()

    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)

    emit_event(
        session_id,
        "hook.invoked",
        {
            "hook": "post_edit_tracker",
            "trigger": "PostToolUse",
            "outcome": "tracked",
            "matched_count": 1,
            "exit_code": 0,
        },
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
