from __future__ import annotations

import os
import secrets
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.core.browser_profiles import BrowserProfile, load_profiles
from app.core.cdp_page import CdpError, CdpPage, devtools, page_targets

_START_PAGE = "data:text/html,<title>Account%20sign-in</title>"


class LaunchError(OSError):
    pass


@dataclass(frozen=True)
class IsolatedBrowser:
    port: int
    env: dict[str, str]


def _overlaps(path: Path, other: Path) -> bool:
    return path.is_relative_to(other) or other.is_relative_to(path)


def _reject_profile_aliases(directory: Path) -> None:
    def fail_walk(error: OSError) -> None:
        raise error

    for parent, dirs, files in os.walk(directory, onerror=fail_walk, followlinks=False):
        for name in (*dirs, *files):
            path = Path(parent) / name
            if path.is_symlink() or path.is_mount():
                raise LaunchError("registered Chrome profile contains a filesystem alias")


def registered_profile_dir(profile: BrowserProfile, provider: str) -> Path:
    if not sys.platform.startswith("linux"):
        raise LaunchError("isolated reauth requires Linux with Xvfb")
    profiles = load_profiles()
    if profiles.get(profile.email) != profile or provider not in profile.providers:
        raise LaunchError("an exact registered account profile for this provider is required")
    if profile.email in {".", ".."} or Path(profile.email).name != profile.email:
        raise LaunchError("an exact registered account profile for this provider is required")
    try:
        root = Path(os.getenv("AGENT_LB_CHROME_ROOT", str(Path.home() / ".agent-lb-primary/browser-profiles")))
        directory = root / profile.dir
        if not root.is_absolute() or ".." in root.parts or ".." in directory.parts or root not in directory.parents:
            raise LaunchError("registered profile must be inside the canonical browser profile root")
        if root.resolve(strict=True) != root or directory.resolve(strict=True) != directory:
            raise LaunchError("registered profile and canonical root cannot use symlinks")
        if root.name != "browser-profiles" or root.parent.name not in {".agent-lb", ".agent-lb-primary"}:
            raise LaunchError("canonical browser profile root must be an agent-lb browser-profiles directory")
        home = Path.home().resolve()
        config = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        ordinary = (
            config / "chromium",
            config / "google-chrome",
            home / ".config/chromium",
            home / ".config/google-chrome",
            Path.home() / "snap/chromium/common/chromium",
            Path.home() / "Library/Application Support/Google/Chrome",
        )
        if (
            not directory.is_dir()
            or directory.is_mount()
            or directory == home
            or directory in home.parents
            or any(
                _overlaps(directory, path.resolve()) or (path.exists() and directory.samefile(path))
                for path in ordinary
            )
            or any(
                _overlaps(directory, other_dir) or _overlaps(directory, other_dir.resolve())
                for other in profiles.values()
                if other.email != profile.email
                for other_dir in (root / other.dir,)
            )
        ):
            raise LaunchError("registered profile is not a distinct account data directory")
    except LaunchError:
        raise
    except (OSError, RuntimeError):
        raise LaunchError("registered profile directory is unavailable") from None
    return directory


def _chrome_executable() -> str:
    if executable := os.getenv("AGENT_LB_CHROME"):
        return executable
    packaged = Path.home() / ".local/bin/google-chrome-stable"
    if os.access(packaged, os.X_OK):
        return str(packaged)
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        if executable := shutil.which(name):
            return executable
    raise LaunchError("Chrome/Chromium was not found; set AGENT_LB_CHROME")


def _chrome_argv(directory: Path, url: str) -> list[str]:
    return [
        _chrome_executable(),
        f"--user-data-dir={directory}",
        "--remote-debugging-port=0",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--ozone-platform=x11",
        f"--app={url}",
    ]


def chrome_argv(profile: BrowserProfile, url: str) -> list[str]:
    directory = registered_profile_dir(profile, profile.providers[0] if profile.providers else "")
    _reject_profile_aliases(directory)
    return _chrome_argv(directory, url)


@contextmanager
def managed_process(args: list[str], **options) -> Iterator[subprocess.Popen]:
    process = subprocess.Popen(args, start_new_session=True, stderr=subprocess.DEVNULL, **options)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _xauthority(path: Path) -> None:
    fields = (b"", b"", b"MIT-MAGIC-COOKIE-1", secrets.token_bytes(16))
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
        stream.write(struct.pack("!H", 65535))
        for field in fields:
            stream.write(struct.pack("!H", len(field)))
            stream.write(field)


def _display(stack: ExitStack, folder: Path) -> dict[str, str]:
    authority = folder / "xauth"
    _xauthority(authority)
    reader, writer = os.pipe()
    try:
        try:
            stack.enter_context(
                managed_process(
                    [
                        "Xvfb",
                        "-displayfd",
                        str(writer),
                        "-screen",
                        "0",
                        "1280x900x24",
                        "-nolisten",
                        "tcp",
                        "-auth",
                        str(authority),
                    ],
                    pass_fds=(writer,),
                    stdout=subprocess.DEVNULL,
                )
            )
        finally:
            os.close(writer)
        number = os.read(reader, 32).strip() if select.select([reader], [], [], 10)[0] else b""
    finally:
        os.close(reader)
    if not number.isdigit():
        raise LaunchError("Xvfb did not allocate a display")
    allowed_env = {"HOME", "PATH", "USER", "LOGNAME", "LANG", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
    env = {key: value for key, value in os.environ.items() if key in allowed_env or key.startswith("LC_")}
    env.update(DISPLAY=f":{number.decode('ascii')}", XAUTHORITY=str(authority))
    return env


def _cdp_identity(path: Path) -> tuple[int, str] | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        port_text, endpoint = path.read_text().splitlines()
        port = int(port_text)
        if 1 <= port <= 65535 and endpoint.startswith("/devtools/browser/"):
            return port, endpoint
    except (OSError, ValueError):
        pass
    return None


@contextmanager
def isolated_browser(
    profile: BrowserProfile, provider: str, *, expected_directory: Path | None = None, seconds: float = 15
) -> Iterator[IsolatedBrowser]:
    directory = registered_profile_dir(profile, provider)
    if expected_directory is not None and directory != expected_directory:
        raise LaunchError("registered profile changed before browser launch")
    if (directory / "SingletonLock").is_symlink() or (directory / "SingletonLock").exists():
        raise LaunchError("registered Chrome profile is already in use or has a lingering lock")
    _reject_profile_aliases(directory)
    port_file = directory / "DevToolsActivePort"
    previous = _cdp_identity(port_file)
    with ExitStack() as stack:
        folder = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="agent-lb-reauth-")))
        env = _display(stack, folder)
        if registered_profile_dir(profile, provider) != directory:
            raise LaunchError("registered profile changed before browser launch")
        _reject_profile_aliases(directory)
        process = stack.enter_context(
            managed_process(
                _chrome_argv(directory, _START_PAGE),
                env=env,
                stdout=subprocess.DEVNULL,
            )
        )
        deadline = time.monotonic() + seconds
        while process.poll() is None and time.monotonic() < deadline:
            identity = _cdp_identity(port_file)
            if identity is not None and identity != previous:
                port, endpoint = identity
                try:
                    actual = devtools(port, "/json/version").get("webSocketDebuggerUrl")
                except CdpError:
                    actual = None
                if actual == f"ws://127.0.0.1:{port}{endpoint}":
                    yield IsolatedBrowser(port=port, env=env)
                    return
            time.sleep(0.1)
        raise LaunchError("Chrome did not open its own DevTools endpoint")


def isolated_page(browser: IsolatedBrowser, url: str) -> CdpPage:
    deadline = time.monotonic() + 5
    while True:
        targets = [target for target in page_targets(browser.port) if target.get("url") == _START_PAGE]
        if len(targets) == 1 and isinstance(targets[0].get("id"), str):
            break
        if time.monotonic() >= deadline:
            raise LaunchError("isolated Chrome did not start with a single app page")
        time.sleep(0.1)
    page = CdpPage(browser.port, target_id=targets[0]["id"], timeout=5)
    try:
        page.navigate(url)
    except BaseException:
        page.detach()
        raise
    return page


def launch_profile(profile: BrowserProfile, url: str) -> None:
    raise LaunchError("unmanaged profile windows are disabled; use account browser reauth on the Linux primary")
