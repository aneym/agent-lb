import os
import subprocess
import sys
from pathlib import Path


def copy_configuration(root: Path, tree: Path) -> None:
    candidates = [*root.glob(".env*"), root / ".npmrc", root / "uv.toml"]
    for directory in ("apps", "packages"):
        candidates.extend((root / directory).glob("*/.env*"))
    for source in candidates:
        relative = source.relative_to(root)
        destination = tree / relative
        if not source.is_file() or source.is_symlink() or destination.exists():
            continue
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", str(relative)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if tracked.returncode == 0:
            continue
        if tracked.returncode != 1:
            raise RuntimeError("could not determine configuration tracking status")
        if destination.is_symlink():
            raise RuntimeError("configuration destination is a dangling symlink")
        destination.parent.mkdir(parents=True, exist_ok=True)
        option = "-c" if sys.platform == "darwin" else "--reflink=auto"
        subprocess.run(["cp", option, "-p", str(source), str(destination)], check=True)


def setup() -> None:
    root = Path(os.environ["T3CODE_PROJECT_ROOT"]).resolve(strict=True)
    tree = Path(os.environ["T3CODE_WORKTREE_PATH"]).resolve(strict=True)
    if Path.cwd().resolve() != tree or Path(__file__).resolve().parent.parent != tree:
        raise RuntimeError("run setup from the T3 worktree")
    copy_configuration(root, tree)
    env = dict(os.environ, UV_LINK_MODE="clone", UV_PROJECT_ENVIRONMENT=str(tree / ".venv"))
    env.pop("UV_NO_SYNC", None)
    cache = Path("/srv/cow/cache/uv" if sys.platform == "linux" else "/Volumes/StudioExt/cache/uv")
    if cache.is_dir():
        env["UV_CACHE_DIR"] = str(cache)
    subprocess.run(
        ["uv", "sync", "--frozen", "--all-extras", "--all-groups"],
        env=env,
        check=True,
    )


if __name__ == "__main__":
    setup()
