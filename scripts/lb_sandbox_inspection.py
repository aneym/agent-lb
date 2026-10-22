from __future__ import annotations
import json
import os
import re
import uuid
from pathlib import Path
from lb_sandbox_account_reader import (
    load_key,
    load_secrets,
    read_accounts,
)
from lb_sandbox_credentials import (
    custody_paths,
    entry_of,
)
from lb_sandbox_filesystem import (
    confined,
    open_dir,
    read_root_json,
    write_in_root,
    write_json,
)
from lb_sandbox_launchd import (
    usable_counts,
)
from lb_sandbox_paths import (
    EXIT_ASSERT,
    EXIT_OK,
    EXIT_UNHEALTHY,
    HOLD_MAX_S,
    Refused,
    now_iso,
    root_for,
)
from lb_sandbox_process_scan import (
    scan_processes,
)
from lb_sandbox_processes import (
    emit,
    http,
    launchd_pid,
    load_meta,
)
from lb_sandbox_protection import (
    is_confined,
)
from lb_sandbox_traversal import (
    scan_paths,
)
from lb_sandbox_volumes import (
    check_own_volume,
    on_own_volume,
)


def cmd_status(args) -> int:
    root, meta = load_meta(args.run_id)
    ports = meta["ports"]
    code, body = http(ports["primary"], "/internal/drain/status", timeout=2)
    in_flight = None
    if code == 200:
        try:
            in_flight = int(json.loads(body)["checks"]["in_flight"])
        except (ValueError, KeyError, TypeError):
            in_flight = None
    aux = read_root_json(root, "state/aux.json")
    accounts = read_accounts(root)
    for account in accounts:
        account.pop("usable", None)
    primary_pid = launchd_pid(meta["label"])
    healthy = (
        http(ports["primary"], "/health/ready")[0] == 200
        and http(ports["front"], "/health/ready")[0] == 200
        and bool(aux.get("ready"))
    )
    out = {
        "run_id": meta["run_id"],
        "healthy": healthy,
        "in_flight_primary": in_flight,
        "preferred_port": read_root_json(root, "state/front.json").get("preferred"),
        "primary_pid": primary_pid,
        "aux_pid": aux.get("pid"),
        "front_pid": aux.get("front_pid"),

        "own_volume": on_own_volume(root),
        "confined": {
            "primary": is_confined(primary_pid),
            "aux": is_confined(aux.get("pid")),
            "front": is_confined(aux.get("front_pid")),
        },
        "accounts": accounts,
        "accounts_per_vendor": usable_counts(read_accounts(root)),
        "gate": aux.get("gate", {"forwarded": [], "refused": 0}),
        "edge": {k: v for k, v in aux.get("edge", {}).items() if k != "log"},
        "edge_log": str(root / "logs" / "edge.jsonl"),

        "request_log_store": str(root / "data" / "store.db"),
        "ports": ports,
    }
    return emit(out, EXIT_OK if healthy else EXIT_UNHEALTHY)


CODEX_PLACEHOLDER = "lb-sandbox-placeholder-not-a-credential"


def cmd_client_env(args) -> int:
    root, meta = load_meta(args.run_id)
    check_own_volume(root)
    front = meta["ports"]["front"]
    if args.vendor == "claude":
        os.close(confined(lambda: open_dir(root, "clients/claude", create=True)))
        env = {
            "CLAUDE_LB_LOCAL_URL": f"http://127.0.0.1:{front}",
            "CLAUDE_LB_BASE_URL": f"http://127.0.0.1:{front}",
            "CLAUDE_LB_LAUNCHD_LABEL": meta["label"],
        }
    else:
        home = root / "clients" / "codex"
        config = (
            "\n".join(
                [
                    'model_provider = "agent-lb"',
                    "",
                    "[model_providers.agent-lb]",
                    'name = "OpenAI"',
                    f'base_url = "http://127.0.0.1:{front}/backend-api/codex"',
                    'wire_api = "responses"',
                    "supports_websockets = false",
                    "requires_openai_auth = true",
                    "",
                ]
            )
        ).encode()

        confined(lambda: write_in_root(root, "clients/codex/config.toml", config))
        confined(
            lambda: write_json(
                root, "clients/codex/auth.json", {"OPENAI_API_KEY": CODEX_PLACEHOLDER, "auth_mode": "apikey"}
            )
        )
        env = {"CODEX_SOURCE_HOME": str(home)}
    return emit({"run_id": meta["run_id"], "vendor": args.vendor, "env": env}, EXIT_OK)


def parse_fault(spec: str) -> tuple[str, int | None]:
    if spec == "account_429":
        return "account_429", None
    match = re.fullmatch(r"cut_after_bytes:([0-9]{1,9})", spec)
    if match:
        return "cut_after_bytes", int(match.group(1))
    match = re.fullmatch(r"hold_stream:([0-9]{1,3})", spec)
    if match and 1 <= int(match.group(1)) <= HOLD_MAX_S:
        return "hold_stream", int(match.group(1))
    raise Refused(f"unknown fault {spec!r}; use account_429, cut_after_bytes:N or hold_stream:S (S 1-{HOLD_MAX_S})")


def cmd_fault(args) -> int:
    root, _ = load_meta(args.run_id)
    check_own_volume(root)
    path = f"state/fault-{args.edge}.json"
    if args.clear:
        confined(lambda: write_json(root, path, {"armed": False, "nonce": uuid.uuid4().hex, "at": now_iso()}))
        return emit({"armed": False, "edge": args.edge, "fault": None}, EXIT_OK)
    fault, n = parse_fault(args.arm)
    if fault == "hold_stream" and args.edge != "anthropic":
        raise Refused("hold_stream is served by the anthropic edge only")
    confined(
        lambda: write_json(
            root, path, {"armed": True, "fault": fault, "n": n, "nonce": uuid.uuid4().hex, "at": now_iso()}
        )
    )
    return emit({"armed": True, "edge": args.edge, "fault": args.arm}, EXIT_OK)


def cmd_scan(args) -> int:
    root = root_for(args.run_id)
    if not args.paths and not args.processes:
        raise Refused("scan needs paths, --processes or both")
    secrets = load_secrets(root if entry_of(root) == "dir" else None)
    key = load_key(root)
    newer = Path(args.newer_than).stat().st_mtime if args.newer_than else None
    result = (
        scan_paths(
            [Path(p) for p in args.paths],
            secrets,
            newer_than=newer,
            exclude=[Path(p) for p in args.exclude],
            skip_exact=custody_paths(root),
            keys=[key] if key else None,
            root=root,
        )
        if args.paths
        else {"found": False, "complete": True, "files_scanned": 0, "hits": []}
    )
    result.pop("_files", None)
    if args.processes:
        procs = scan_processes(root, args.run_id, secrets)
        result["processes"] = procs
        result["found"] = result["found"] or procs["found"]
        result["complete"] = result["complete"] and procs["complete"]
    result["secrets_loaded"] = len(secrets)
    result["ciphertext_keys"] = 1 if key else 0
    result["run_id"] = args.run_id
    return emit(result, EXIT_OK if result["complete"] and not result["found"] else EXIT_ASSERT)
