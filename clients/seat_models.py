from __future__ import annotations

import os
import re
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

from seat_environment import (  # pyrefly: ignore[missing-import]
    account_env,
)
from seat_process import (  # pyrefly: ignore[missing-import]
    RunControl,
    run_stoppable,
)
from seat_storage import (  # pyrefly: ignore[missing-import]
    DEVIN_FREE_MODEL,
    PROBE_TIMEOUT_SECONDS,
    SEAT_CURSOR_MODELS,
    cli_bin,
    fail,
    route_command,
)


def resolve_model(vendor: str, model: str, control: RunControl | None = None) -> str:
    try:
        completed = run_stoppable(route_command("resolve", model), control, input=None, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as error:
        if control is not None and control.stop_reason():
            return model
        fail(f"route resolve {model} failed: {error}")
    if control is not None and control.stop_reason():
        return model
    if completed.returncode != 0:  # pyrefly: ignore[unbound-name]
        fail(completed.stderr.strip() or f"route resolve {model} exited {completed.returncode}")
    resolved = completed.stdout.strip().splitlines()[-1]
    if vendor == "devin" and not DEVIN_FREE_MODEL.match(resolved):
        fail(f"{resolved} is billed per token on Devin; the devin seat runs only SWE-2 (swe-2-high|medium|max)")
    return resolved


def cursor_models(account: dict[str, Any], control: RunControl | None = None) -> tuple[str, ...]:
    account_id = account["id"]
    if account_id in SEAT_CURSOR_MODELS:
        return SEAT_CURSOR_MODELS[account_id]
    SEAT_CURSOR_MODELS[account_id] = ()
    try:
        result = run_stoppable(
            [cli_bin("cursor"), "models"],
            control,
            input=None,
            env=account_env(account),
            stdin=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result.stdout)
    ids = [line.split(" - ", 1)[0].strip() for line in text.splitlines() if " - " in line]
    SEAT_CURSOR_MODELS[account_id] = tuple(dict.fromkeys(value for value in ids if re.fullmatch(r"[\w.\-]+", value)))
    return SEAT_CURSOR_MODELS[account_id]


def check_cursor_model(
    model: str, candidates: list[dict[str, Any]], control: RunControl | None = None
) -> list[dict[str, Any]]:
    lists = []
    for account in candidates:
        if control and control.stop_reason():
            return candidates
        lists.append((account, cursor_models(account, control)))
    if control and control.stop_reason():
        return candidates
    listed = tuple(dict.fromkeys(value for _, models in lists for value in models))
    family = re.match(r"^(claude-(?:opus|sonnet)|[a-z]+)-", model)
    prefix = family.group(1) + "-" if family else model.split("-", 1)[0] + "-"
    older = re.fullmatch(r"claude-(opus|sonnet)-5-(?!5(?:-|$)).*?(low|medium|high|xhigh)", model)
    if older:
        name, effort = older.groups()
        pattern = re.compile(rf"^claude-{name}-(\d+)(?:-(\d{{1,2}}))?-{effort}$")
        newer_models: list[tuple[tuple[int, int], str]] = []
        for value in listed:
            match = pattern.fullmatch(value)
            if match and (int(match[1]), int(match[2] or 0)) > (5, 0):
                newer_models.append(((int(match[1]), int(match[2] or 0)), value))
        newest = max(newer_models)[1] if newer_models else f"claude-{name}-5-5-{effort}"
        print(f"seat: {model} is superseded by {newest}", file=sys.stderr)
    supported = [account for account, models in lists if not models or model in models]
    if not supported:
        route_path = os.environ.get("ROUTE_BIN") or str(Path.home() / ".agent-lb" / "bin" / "route")
        closest = [value for value in listed if value.startswith(prefix)]
        try:
            router = runpy.run_path(route_path)
            retired = router["retired_patterns"](router["load_table"]())
            closest = [value for value in closest if not router["is_retired"](value, retired)]
        except (Exception, SystemExit):
            pass
        closest = closest[:3]
        fail(f"{model} is not a Cursor model; closest: {', '.join(closest) or 'none'}")
    return supported


def build_command(
    vendor: str, model: str, mode: str, prompt_file: Path, export_file: Path, effort: str = "medium"
) -> list[str]:
    if vendor == "cursor":
        command = [cli_bin("cursor"), "-p", "--trust", "--output-format", "json", "--model", model]
        command += ["--mode", "ask"] if mode == "ask" else ["--force"]
        return command
    if vendor == "muse":
        if sys.platform != "darwin":
            fail("Muse account login is Studio-local; box path is parked")
        return [
            cli_bin("muse"),
            "exec",
            "--json",
            "--trust-workspace",
            "--model",
            model,
            "--effort",
            effort,
            "--approval-mode",
            "never",
            "--contributor-mode",
            "off",
            "--no-foreign-personal-context",
            *([] if mode == "ask" else ["--disable-sandbox"]),
            "--prompt-file",
            str(prompt_file),
        ]

    permission = "smart" if mode == "ask" else "dangerous"
    return [
        cli_bin("devin"),
        "-p",
        "--model",
        model,
        "--permission-mode",
        permission,
        "--export",
        str(export_file),
        "--prompt-file",
        str(prompt_file),
    ]
