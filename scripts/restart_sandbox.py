from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from scripts.restart_context import RestartContext, SandboxRefused
from scripts.restart_paths import inside, lexical, open_sandbox_root, real_home, refuse_links_on, trusted_file


def sandbox_bindings(context: RestartContext, config: dict, home: Path | None = None) -> dict[str, object]:
    missing = [key for key in context.SANDBOX_KEYS if key not in config]
    if missing:
        raise SandboxRefused(f"sandbox config lacks {', '.join(missing)}")
    sandboxes = lexical(context, (home or real_home(context)) / ".agent-lb" / "sandboxes")
    root = Path(str(config["root"])).expanduser()
    if not root.is_absolute():
        raise SandboxRefused("sandbox root must be absolute")
    root = lexical(context, root)
    if root.parent != sandboxes or root.name in ("", ".", ".."):
        raise SandboxRefused(f"sandbox root {root} is not directly under {sandboxes}")
    refuse_links_on(context, "root", root, "dir")
    label = str(config["label"])
    suffix = label.removeprefix(context.SANDBOX_LABEL_PREFIX)
    if not label.startswith(context.SANDBOX_LABEL_PREFIX) or not suffix or (not suffix.replace("-", "").isalnum()):
        raise SandboxRefused(f"label {label!r} is not a {context.SANDBOX_LABEL_PREFIX}* drill label")
    ports = []
    for key in ("front_port", "primary_port", "standby_port"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise SandboxRefused(f"{key} must be an integer")
        if value < context.SANDBOX_MIN_PORT or value > 2599 or value in context.SANDBOX_FORBIDDEN_PORTS:
            raise SandboxRefused(f"{key} {value} is a live or unsafe port")
        ports.append(value)
    if len(set(ports)) != len(ports):
        raise SandboxRefused("sandbox ports repeat")
    paths = {}
    for key in ("plist", "runtime", "state_dir"):
        path = Path(str(config[key])).expanduser()
        path = lexical(context, path if path.is_absolute() else root / path)
        if root not in path.parents:
            raise SandboxRefused(f"{key} {path} is outside the sandbox root")
        paths[key] = path
    state_dir = paths["state_dir"]
    derived = {
        "BACKUPS": root / "backups",
        "SYNC_LOG": root / "logs" / "sync.log",
        "LOCK_FILE": root / "lb-restart.lock",
        "PREFERRED_FILE": state_dir / "front-preferred-port",
        "PREFERRED_TMP": state_dir / "front-preferred-port.tmp",
        "FRONT_STATE": state_dir / "front.json",
        "STANDBY_PIDFILE": state_dir / "lb-standby.pid",
        "STANDBY_LOG": root / "logs" / "agent-lb-standby.log",
        "WATCHDOG_PAUSE": root / "watchdog.pause",
    }
    for name, path in (("RUNTIME", paths["runtime"]), ("STATE_DIR", state_dir), ("BACKUPS", derived["BACKUPS"])):
        refuse_links_on(context, name, path, "dir")
    refuse_links_on(context, "data", root / "data", "dir")
    for leaf in ("store.db", "encryption.key"):
        refuse_links_on(context, "data", root / "data" / leaf, "file")
    for name, path in derived.items():
        if name != "BACKUPS":
            refuse_links_on(context, name, path, "file")
    refuse_links_on(context, "plist", paths["plist"], "file")
    return {
        "SANDBOX_ROOT": root,
        "RUNTIME": paths["runtime"],
        "STATE_DIR": state_dir,
        **derived,
        "LABEL": label,
        "FRONT_LABEL": None,
        "PLIST": paths["plist"],
        "FRONT_PORT": ports[0],
        "PRIMARY_PORT": ports[1],
        "STANDBY_PORT": ports[2],
    }


def sandbox_bootstrap(context: RestartContext) -> Path:
    return context.entry.parent / "lb-sandbox"


def check_sandbox_bootstrap(context: RestartContext, root: Path) -> None:
    script = sandbox_bootstrap(context)
    if inside(context, script, root.parent):
        raise SandboxRefused(f"sandbox bootstrap {script} is inside the sandboxes dir")
    refuse_links_on(context, "bootstrap", script, "file")
    try:
        info = os.lstat(script)
    except OSError as exc:
        raise SandboxRefused(f"sandbox bootstrap {script} is missing") from exc
    trusted_file(context, "sandbox bootstrap", script, info)


def sandbox_python_for(context: RestartContext, root: Path) -> Path:
    return root.parent.parent / "runtime" / "agent-lb" / ".venv" / "bin" / "python"


def sandbox_venv_base_dir(context: RestartContext, python: Path) -> Path:
    cfg = python.parent.parent / "pyvenv.cfg"
    try:
        fd = os.open(cfg, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise SandboxRefused(f"interpreter {python} has no venv config {cfg}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SandboxRefused(f"venv config {cfg} is not a single-link regular file")
        trusted_file(context, "venv config", cfg, info)
        text = os.read(fd, 1 << 16).decode(errors="replace")
    finally:
        os.close(fd)
    home = ""
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().lower() == "home":
            home = value.strip()
    if not home.startswith("/"):
        raise SandboxRefused(f"venv config {cfg} names no absolute home")
    base = Path(os.path.realpath(home))
    try:
        trusted_file(context, "venv home", base, os.stat(base))
    except OSError as exc:
        raise SandboxRefused(f"venv home {home} is missing") from exc
    return base


def check_sandbox_interpreter(context: RestartContext, python: Path, root: Path) -> None:
    python = lexical(context, python)
    sandboxes = root.parent
    if python != sandbox_python_for(context, root) or inside(context, python, sandboxes):
        raise SandboxRefused(f"interpreter {python} is not the installed runtime's venv python")
    refuse_links_on(context, "interpreter", python.parent, "dir")
    base = sandbox_venv_base_dir(context, python)
    try:
        target = Path(os.path.realpath(python))
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise SandboxRefused(f"interpreter {python} is missing ({exc.__class__.__name__})") from exc
    try:
        info = os.fstat(fd)
        head = os.read(fd, 4) if stat.S_ISREG(info.st_mode) else b""
    finally:
        os.close(fd)
    if inside(context, target, sandboxes) or inside(context, target, root):
        raise SandboxRefused(f"interpreter {python} leads into a sandbox root")
    if target.parent != base or not context.BASE_INTERPRETER_NAME.match(target.name):
        raise SandboxRefused(f"interpreter {python} does not lead to the base interpreter its venv config names")
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & stat.S_IXUSR or head not in context.NATIVE_MAGIC:
        raise SandboxRefused(f"interpreter {python} does not lead to a native executable (a script or shim?)")
    trusted_file(context, "interpreter", target, info)


def sandbox_program(context: RestartContext, root: Path) -> list[str]:
    return [str(sandbox_python_for(context, root)), "-I", str(sandbox_bootstrap(context)), "_serve", str(root)]


def has_control_characters(context: RestartContext, value: object) -> bool:
    if isinstance(value, str):
        return any((ord(ch) < 32 or ord(ch) == 127 for ch in value))
    if isinstance(value, dict):
        return any((has_control_characters(context, k) or has_control_characters(context, v) for k, v in value.items()))
    if isinstance(value, list):
        return any((has_control_characters(context, v) for v in value))
    return False


def check_sandbox_plist(context: RestartContext, data: object, root: Path, label: str, primary_port: int) -> None:
    if not isinstance(data, dict):
        raise SandboxRefused("sandbox plist is not a dictionary")
    if has_control_characters(context, data):
        raise SandboxRefused("sandbox plist has a control character in a key or value")
    extra = sorted((str(key) for key in set(data) - context.SANDBOX_PLIST_KEYS))
    if extra:
        raise SandboxRefused(f"sandbox plist has keys lb-sandbox never writes: {', '.join(extra)}")
    want = [*sandbox_program(context, root), "--host", "127.0.0.1", "--port", str(primary_port)]
    if data.get("Label") != label or data.get("ProgramArguments") != want:
        raise SandboxRefused("sandbox plist does not run `<venv python> -I <installed lb-sandbox> _serve <root>`")
    env = data.get("EnvironmentVariables")
    if not isinstance(env, dict):
        raise SandboxRefused("sandbox plist has no environment")
    if "AGENT_LB_FEDERATION_TOKEN" in env:
        raise SandboxRefused("sandbox plist carries the federation token")
    unknown = sorted(
        (
            str(k)
            for k in env
            if k not in context.SANDBOX_ENV_KEYS and (not str(k).startswith(context.SANDBOX_ENV_PREFIX))
        )
    )
    if unknown:
        raise SandboxRefused(f"sandbox plist env has keys lb-sandbox never writes: {', '.join(unknown)}")
    fixed = {
        "HOME": str(root / "home"),
        "PATH": context.SANDBOX_PATH_ENV,
        "PYTHONPATH": str(root / "runtime"),
        "LB_SANDBOX_ROOT": str(root),
        "AGENT_LB_DATA_DIR": str(root / "data"),
        "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{root / 'data' / 'store.db'}",
        "AGENT_LB_ENCRYPTION_KEY_FILE": str(root / "data" / "encryption.key"),
    }
    for key, value in fixed.items():
        if env.get(key) != value:
            raise SandboxRefused(f"sandbox plist {key} is not the sandbox's own")
    for store in (root / "data", root / "data" / "store.db", root / "data" / "encryption.key"):
        if not inside(context, store.resolve(), root):
            raise SandboxRefused(f"sandbox store {store} resolves outside the sandbox root (a link?)")
    workdir = data.get("WorkingDirectory")
    if workdir is not None and (not inside(context, Path(str(workdir)).resolve(), root)):
        raise SandboxRefused("sandbox plist working directory is outside the sandbox root")
    for key in ("StandardOutPath", "StandardErrorPath"):
        if key in data and (not inside(context, Path(str(data[key])).resolve(), root)):
            raise SandboxRefused(f"sandbox plist {key} is outside the sandbox root")
    cwd = Path(str(workdir)) if workdir is not None else root / "runtime"
    for key, value in env.items():
        text = str(value)
        if key == "PATH" or key in fixed:
            continue
        if "://" in text:
            match = context.SANDBOX_PORT_URL.match(text)
            port = int(match.group(1)) if match else 0
            if port < context.SANDBOX_MIN_PORT or port > 2599 or port in context.SANDBOX_FORBIDDEN_PORTS:
                raise SandboxRefused(f"sandbox plist {key} points outside the sandbox's ports")
            continue
        for part in text.split(":"):
            port = context.SANDBOX_LEADING_PORT.match(part)
            if port and int(port.group(1)) in context.SANDBOX_FORBIDDEN_PORTS:
                raise SandboxRefused(f"sandbox plist {key} names a live port")
            if part.startswith("~"):
                raise SandboxRefused(f"sandbox plist {key} names a home-relative path")
            if part.startswith("/") or "/" in part or part in (".", ".."):
                if not inside(context, (cwd / part).resolve(), root):
                    raise SandboxRefused(f"sandbox plist {key} names a path outside the sandbox root")


def read_sandbox_config(context: RestartContext, config_path: Path) -> tuple[dict, int, os.stat_result]:
    path = lexical(context, Path(config_path).expanduser().absolute())
    sandboxes = lexical(context, real_home(context) / ".agent-lb" / "sandboxes")
    if (
        path.name != context.SANDBOX_CONFIG_NAME
        or path.parent.parent != sandboxes
        or path.parent.name in ("", ".", "..")
    ):
        raise SandboxRefused(f"sandbox config {path} is not {sandboxes}/<run>/{context.SANDBOX_CONFIG_NAME}")
    root_fd, root_info = open_sandbox_root(context, path.parent)
    try:
        try:
            fd = os.open(context.SANDBOX_CONFIG_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        except OSError as exc:
            raise SandboxRefused(f"sandbox config {path} is a link or unreadable ({exc.__class__.__name__})") from exc
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or (info.st_dev != root_info.st_dev)
            ):
                raise SandboxRefused(f"sandbox config {path} is not a single-link file of this user in the root")
            data = os.read(fd, context.SANDBOX_CONFIG_MAX + 1)
        finally:
            os.close(fd)
        if len(data) > context.SANDBOX_CONFIG_MAX:
            raise SandboxRefused(f"sandbox config {path} is larger than any config lb-sandbox writes")
        try:
            config = json.loads(data.decode())
        except ValueError as exc:
            raise SandboxRefused(f"cannot read sandbox config {path}: {exc.__class__.__name__}") from exc
        if not isinstance(config, dict):
            raise SandboxRefused("sandbox config is not a JSON object")
        if lexical(context, Path(str(config.get("root", "")))) != path.parent:
            raise SandboxRefused(f"sandbox config {path} names another root")
    except BaseException:
        os.close(root_fd)
        raise
    return (config, root_fd, root_info)
