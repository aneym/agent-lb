from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

CLIENT = Path(".claude/hooks/dispatch/hook-client")
DAEMON = Path(".claude/hooks/hookd.py")
LABEL = "com.aneym.hookd"
PLIST = Path("Library/LaunchAgents") / f"{LABEL}.plist"
PLAIN_COMMAND = (
    'P="$HOME/.local/share/uv/python/cpython-3.14-macos-aarch64-none/bin/python3.14"; '
    '[ -x "$P" ] || P=python3; '
    'exec "$P" -I -S "$HOME/.claude/{script}" {event} \'{key}\' {rev}'
)
CLIENT_COMMAND = "".join(
    (
        'C="$HOME/.claude/hooks/dispatch/hook-client"; ',
        'if [ -x "$C" ]; then exec "$C" "$HOME/.claude/{script}" {event} \'{key}\' {rev}; ',
        "else ",
        PLAIN_COMMAND,
        "; fi",
    )
)
TEMPLATE = [PLAIN_COMMAND]
OWN_PARENT = ("Stop",)


def build_client(source: Path, home: Path) -> bool:
    code = (source / "hooks" / "hook-client.c").read_bytes()
    target = home / CLIENT
    stamp = target.with_name("hook-client.sha256")
    digest = hashlib.sha256(code).hexdigest()
    if target.is_file() and os.access(target, os.X_OK) and stamp.is_file() and stamp.read_text().strip() == digest:
        return True
    compiler = next(
        (path for path in ("/usr/bin/cc", shutil.which("cc"), shutil.which("gcc")) if path and os.path.exists(path)),
        None,
    )
    if compiler is None:
        print("hookd: no C compiler; hooks run the dispatcher directly")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".hook-client.{os.getpid()}")
    src = target.with_name(f".hook-client.{os.getpid()}.c")
    src.write_bytes(code)
    try:
        built = subprocess.run(
            [compiler, "-O2", "-o", str(staged), str(src)], capture_output=True, text=True, timeout=120
        )
    finally:
        src.unlink(missing_ok=True)
    if built.returncode != 0:
        staged.unlink(missing_ok=True)
        print(f"hookd: client build failed; hooks run the dispatcher directly: {built.stderr.strip()[-300:]}")
        return False
    os.replace(staged, target)
    stamp.write_text(digest + "\n")
    print(f"hookd: built {target}")
    return True


def served(home: Path) -> bool:
    return sys.platform == "darwin" and home.resolve() == Path.home().resolve()


def prepare(source: Path, home: Path, build: bool) -> None:
    client_ready = (home / CLIENT).is_file() and os.access(home / CLIENT, os.X_OK)
    if build and served(home):
        client_ready = build_client(source, home)
    TEMPLATE[0] = CLIENT_COMMAND if served(home) and client_ready else PLAIN_COMMAND


def command_for(script: str, event: str, key: str, rev: str) -> str:
    template = PLAIN_COMMAND if event in OWN_PARENT else TEMPLATE[0]
    return template.format(script=script, event=event, key=key, rev=rev)


def plist_text(home: Path, python: str) -> str:
    log = home / ".claude/hooks/dispatch/hookd.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>{home / DAEMON}</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>HOME</key><string>{home}</string><key>LANG</key><string>en_US.UTF-8</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def ensure_daemon(home: Path, restart: bool) -> None:
    if not served(home) or not (home / DAEMON).is_file():
        return
    domain = f"gui/{os.getuid()}"
    path = home / PLIST
    wanted = plist_text(home, sys.executable)
    changed = not path.is_file() or path.read_text() != wanted
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wanted)
    loaded = (
        subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], capture_output=True, timeout=30).returncode == 0
    )
    if loaded and changed:
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True, timeout=30)
        loaded = False
    if not loaded:
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True, timeout=30
        )
        print(f"hookd: {'started' if result.returncode == 0 else 'start failed: ' + result.stderr.strip()[-200:]}")
    elif restart:
        subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], capture_output=True, timeout=30)
        print("hookd: restarted")
