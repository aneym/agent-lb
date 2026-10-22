from __future__ import annotations  # noqa: I001


import hashlib
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from clients.claude_lb_config import CCGPT_MODE
from clients.claude_lb_config import CCGPT_MODEL
from clients.claude_lb_config import CCGPT_MODEL_NAME
from clients.claude_lb_config import DEFAULT_CLAUDE_EFFORT
from clients.claude_lb_config import DEFAULT_CLAUDE_MODEL
from clients.claude_lb_config import ENTRY_PATH


def arg_value(argv: list[str], *names: str) -> str | None:
    for index, value in enumerate(argv):
        for name in names:
            if value == name and index + 1 < len(argv):
                return argv[index + 1]
            prefix = f"{name}="
            if value.startswith(prefix):
                return value[len(prefix) :]
    return None


def requested_model(argv: list[str]) -> str:
    return arg_value(argv, "--model", "-m") or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_CLAUDE_MODEL


def requested_effort(argv: list[str]) -> str:
    return arg_value(argv, "--effort") or os.environ.get("CC_EFFORT_LEVEL") or DEFAULT_CLAUDE_EFFORT


def anthropic_quota_key(model: str, effort: str) -> tuple[str, str]:
    normalized_model = model.lower()
    normalized_effort = effort.lower()
    if "haiku" in normalized_model:
        return "anthropic_standard", "standard"
    if normalized_effort not in {"", "none", "off"}:
        return "anthropic_top_thinking", "top-thinking"
    return "anthropic_top", "top-model"


class CcgptModelViolation(ValueError):
    pass


def ccgpt_upstream_path(path: str, body: bytes | None = None) -> str:
    if not CCGPT_MODE:
        return path
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return path
    exact_paths = {
        "/v1/messages",
        "/v1/messages/",
        "/v1/messages/count_tokens",
        "/v1/messages/count_tokens/",
    }
    if parsed.path not in exact_paths:
        return path
    normalized = parsed.path[:-1] if parsed.path.endswith("/") else parsed.path
    suffix = path[len(parsed.path) :]
    trailing_slash = "/" if parsed.path.endswith("/") else ""
    if normalized == "/v1/messages/count_tokens":
        return f"/v1/ccgpt/messages/count_tokens{trailing_slash}{suffix}"
    requested = ccgpt_request_model(body)
    if not requested or not CCGPT_MODEL_NAME.match(requested):
        label = requested or "missing model"
        raise CcgptModelViolation(f"CCGPT only permits GPT models; rejected Messages request for {label}")
    return f"/v1/ccgpt/messages{trailing_slash}{suffix}"


def ccgpt_request_model(body: bytes | None) -> str | None:
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    model = payload.get("model") if isinstance(payload, dict) else None
    return model if isinstance(model, str) else None


def without_option(argv: list[str], option: str) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for argument in argv:
        if skip_next:
            skip_next = False
            continue
        if argument == option:
            skip_next = True
            continue
        if argument.startswith(option + "="):
            continue
        cleaned.append(argument)
    return cleaned


def claude_executable() -> str:
    own = ENTRY_PATH
    shadowed = False
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        candidate = Path(entry or ".") / "claude"
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            continue
        if candidate.resolve() == own:
            shadowed = True
            continue
        return str(candidate) if shadowed else "claude"
    if shadowed:
        print(
            f"cc: every claude on PATH is this launcher ({own}); refusing to start itself. "
            "Put the real Claude Code binary on PATH.",
            file=sys.stderr,
        )
        raise SystemExit(127)
    return "claude"


def build_command(argv: list[str]) -> list[str]:
    command = [claude_executable()]
    if CCGPT_MODE:
        argv = without_option(without_option(argv, "--model"), "--effort")
        command.extend(["--model", CCGPT_MODEL, "--effort", "high"])
    else:
        if arg_value(argv, "--model", "-m") is None and not os.environ.get("ANTHROPIC_MODEL"):
            command.extend(["--model", DEFAULT_CLAUDE_MODEL])
        effort_arg = arg_value(argv, "--effort")
        effort_env = (os.environ.get("CC_EFFORT_LEVEL") or "").strip()

        if effort_arg is None:
            if effort_env.lower() not in {"", "none", "off"}:
                command.extend(["--effort", effort_env])
            elif effort_env.lower() in {"none", "off"}:
                pass
            else:
                command.extend(["--effort", DEFAULT_CLAUDE_EFFORT])
        elif effort_arg.lower() in {"none", "off"}:
            argv = without_option(argv, "--effort")
    permission_mode = None if CCGPT_MODE else os.environ.get("CC_PERMISSION_MODE")
    if CCGPT_MODE and not has_permission_option(argv):
        permission_mode = "bypassPermissions"
    if permission_mode:
        command.extend(["--permission-mode", permission_mode])
    command.extend(argv)
    return command


def has_permission_option(argv: list[str]) -> bool:
    return any(
        argument == "--permission-mode"
        or argument.startswith("--permission-mode=")
        or argument == "--dangerously-skip-permissions"
        for argument in argv
    )


def headless_invocation(argv: list[str]) -> bool:
    return "-p" in argv or "--print" in argv


def derive_session_id(argv: list[str]) -> str:

    explicit = os.environ.get("CLAUDE_LB_SESSION_ID")
    if explicit:
        return explicit
    if headless_invocation(argv):
        digest = hashlib.sha256(os.getcwd().encode()).hexdigest()[:12]
        return f"cc-headless-{digest}"
    return f"cc-{os.getpid()}-{int(time.time())}"


def auto_resume_enabled() -> bool:
    value = (os.environ.get("CLAUDE_LB_AUTO_RESUME") or "1").strip().lower()
    return value not in {"0", "never", "off", "false"}


def build_resume_command(argv: list[str], claude_session_id: str) -> list[str]:
    command = [claude_executable()]
    effort = (os.environ.get("CC_EFFORT_LEVEL") or "").strip()
    if effort and effort.lower() not in {"none", "off"}:
        command.extend(["--effort", effort])
    permission_mode = os.environ.get("CC_PERMISSION_MODE")
    if permission_mode:
        command.extend(["--permission-mode", permission_mode])
    for flag in ("--model", "--output-format"):
        value = arg_value(argv, flag)
        if value:
            command.extend([flag, value])
    command.extend(
        [
            "--resume",
            claude_session_id,
            "-p",
            "You were interrupted by a usage limit that has now reset. Continue the task exactly where you left off.",
        ]
    )
    return command


def claude_session_args(argv: list[str], command: list[str]) -> tuple[list[str], str | None]:
    explicit_id = arg_value(argv, "--session-id")
    if explicit_id is not None:
        return command, explicit_id
    if "--fork-session" not in argv:
        resume_id = arg_value(argv, "--resume", "-r")
        if resume_id is not None:
            return command, resume_id
        if "--continue" in argv or "-c" in argv:
            return command, None
    session_id = str(uuid.uuid4())
    return [*command, "--session-id", session_id], session_id
