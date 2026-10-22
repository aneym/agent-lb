from __future__ import annotations

import argparse
import os
from pathlib import Path

from .credentials import (
    check_data_custody,
    live_federation_token,
    open_data_dir,
)
from .filesystem import (
    open_in_root,
    read_root_json,
)
from .paths import (
    EXIT_UNHEALTHY,
    Refused,
    Unhealthy,
    bootstrap_script,
    check_ports,
)
from .private_storage import (
    check_root,
)
from .protection import (
    check_bootstrap,
    check_interpreter,
    interpreter_for,
)
from .runtime import (
    log_to_root,
    sandbox_env,
    token_pipe,
)
from .volumes import (
    check_own_volume,
)


def serve_exec_args(root: Path, argv: list[str]) -> tuple[str, list[str], dict[str, str], str]:

    root = check_root(root)
    meta = read_root_json(root, "sandbox.json")
    if not meta or meta.get("run_id") != root.name:
        raise Refused(f"{root} has no sandbox.json stamped with its run id")
    parser = argparse.ArgumentParser(prog="lb-sandbox _serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    opts, extra = parser.parse_known_args(argv)
    if extra:
        raise Refused(f"_serve takes only --host and --port, not {' '.join(extra)}")
    if opts.host != "127.0.0.1":
        raise Refused("the sandbox binds 127.0.0.1 only")
    expected_ports = {"front", "primary", "standby", "gate", "edge_anthropic", "edge_openai"}
    if not isinstance(meta.get("ports"), dict) or set(meta["ports"]) != expected_ports:
        raise Refused("sandbox metadata must contain exactly the six listener ports")
    check_ports(list(meta["ports"].values()))

    for relative in (
        "data",
        "data/store.db",
        "data/encryption.key",
        "data/federation-push.json",
        "data/conversation-archive",
    ):
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise Refused(f"sandbox data path {relative} is redirected outside the root")

    check_data_custody(root)
    try:
        key_fd = open_in_root(root, "data/encryption.key", os.O_RDONLY)
    except OSError as exc:
        raise Refused(f"sandbox store key is missing or redirected ({exc.__class__.__name__})") from None
    os.close(key_fd)

    try:
        data_fd = open_data_dir(root)
    except OSError as exc:
        raise Refused(f"sandbox data dir is missing or redirected ({exc.__class__.__name__})") from None
    data = os.fstat(data_fd)
    os.close(data_fd)
    allowed = {meta["ports"]["primary"], meta["ports"]["standby"]}
    check_ports([opts.port])
    if opts.port not in allowed:
        raise Refused(f"port {opts.port} is not this sandbox's primary or standby port")
    if os.environ.get("LB_SANDBOX_ROOT") != str(root):
        raise Refused("LB_SANDBOX_ROOT does not match the sandbox root")

    env = sandbox_env(root, meta["ports"], root.name, meta.get("transport", "http"))
    env.update({k: os.environ[k] for k in ("USER", "LOGNAME", "TMPDIR", "LANG") if k in os.environ})
    token = live_federation_token()
    if not token:
        raise Unhealthy("live plist has no federation token")
    python = str(interpreter_for(root))
    check_interpreter(Path(python), root)
    script = bootstrap_script()
    check_bootstrap(script, root)

    args = [
        python,
        "-I",
        str(script),
        "_boot",
        str(root),
        "--data-id",
        f"{data.st_dev}:{data.st_ino}",
        "--host",
        "127.0.0.1",
        "--port",
        str(opts.port),
    ]
    return python, args, env, token


def cmd_serve(root: str, argv: list[str]) -> int:
    root_path = check_root(Path(root))
    log_to_root(root_path, "logs/primary.log")
    check_own_volume(root_path)
    python, args, env, token = serve_exec_args(root_path, argv)
    read_fd = token_pipe(token)
    del token
    os.chdir(root_path / "runtime")
    os.execve(python, [*args[:5], "--token-fd", str(read_fd), *args[5:]], env)
    return EXIT_UNHEALTHY
