from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

REMOTE_CLEANUP_GRACE_S = 75

REMOVE_CHECKOUT = """import os,re,subprocess,sys
from pathlib import Path

def remove_checkout(repo, scratch, decision):
 root=Path.home()/".cache/route-run"
 path=Path(scratch)
 if not re.fullmatch(r"dcn_[A-Za-z0-9]+",decision) or path != root/decision:
  raise ValueError("refused cleanup outside the decision scratch checkout")
 if path.exists():
  subprocess.run(["git","-C",repo,"worktree","remove","--force",str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
"""


def git_value(cwd: Path, *argv: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *argv], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise ValueError("cwd must be a git checkout")
    return result.stdout.strip()


def validate_worktree(cwd: Path) -> None:
    root = Path(git_value(cwd, "rev-parse", "--show-toplevel")).resolve()
    git_dir = Path(git_value(cwd, "rev-parse", "--absolute-git-dir")).resolve()
    common = Path(git_value(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if root != cwd or git_dir == common:
        raise ValueError("--write requires --cwd at the root of a linked git worktree, not a main checkout")


def ssh_python(host: str, script: str, *args: str) -> list[str]:
    command = shlex.join(["python3", "-c", script, *args])
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, "bash -lc " + shlex.quote(command)]


def prepare_remote(cwd: Path, row: dict) -> dict:
    try:
        root = Path(git_value(cwd, "rev-parse", "--show-toplevel"))
    except ValueError:
        raise ValueError(
            "--cwd is not a git checkout; remote read-only runs map a repo checkout to $HOME/repos/<name>"
        ) from None
    if git_value(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError(
            "caller tree is dirty (tracked or untracked changes); remote read-only runs require a clean tree"
        )
    sha = git_value(root, "rev-parse", "HEAD")
    refs = git_value(root, "for-each-ref", "--format=%(refname)", "--contains=" + sha, "refs/remotes/origin/")
    if not refs:
        raise ValueError(
            "caller HEAD is unpushed (not reachable from origin); remote read-only runs require a pushed HEAD"
        )
    origin = git_value(root, "remote", "get-url", "origin")
    name = origin.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("origin has no usable repository name for a matching remote checkout")
    script = (
        REMOVE_CHECKOUT
        + """import json
name,environment,sha,decision,prefix=sys.argv[1:]
repo=(Path("/Volumes/StudioExt/repos") if environment=="studio" else Path.home()/"repos")/name
scratch=Path.home()/".cache/route-run"/decision
created=False
try:
 if not (repo/".git").exists():
  raise ValueError("selected host has no matching repo checkout")
 fetched=subprocess.run(["git","-C",str(repo),"fetch","-q","origin",sha],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45)
 if fetched.returncode:
  raise ValueError("selected host could not fetch caller HEAD from origin")
 scratch.parent.mkdir(parents=True,exist_ok=True)
 if scratch.exists():
  raise ValueError("decision scratch checkout already exists")
 created=True
 added=subprocess.run(["git","-C",str(repo),"worktree","add","--detach",str(scratch),sha],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
 if added.returncode:
  raise ValueError("selected host could not create the decision scratch checkout")
 execution=scratch/prefix
 if not execution.is_dir():
  raise ValueError("caller directory is absent from the fetched checkout")
 actual=subprocess.check_output(["git","-C",str(execution),"rev-parse","HEAD"],stderr=subprocess.DEVNULL,text=True,timeout=10).strip()
 if actual != sha:
  raise ValueError("remote scratch checkout does not match caller HEAD")
 print(json.dumps(dict(remote_repo=str(repo),remote_scratch=str(scratch),execution_cwd=str(execution),execution_sha=actual)))
except (OSError,ValueError,subprocess.SubprocessError) as error:
 if created:
  remove_checkout(str(repo),str(scratch),decision)
 print(json.dumps(dict(error=str(error) if isinstance(error,ValueError) else "remote checkout preparation failed")))
 sys.exit(1)
"""
    )
    try:
        response = subprocess.run(
            ssh_python(
                row["ssh_host"], script, name, row["environment"], sha, row["decision_id"], str(cwd.relative_to(root))
            ),
            capture_output=True,
            text=True,
            timeout=110,
        )
        prepared = json.loads(response.stdout)
        if response.returncode:
            raise ValueError(prepared.get("error") or "remote checkout preparation failed")
        return prepared
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        raise ValueError("selected host unavailable while preparing caller checkout") from None


def cleanup_remote(row: dict) -> None:
    if not row.get("remote_scratch"):
        return
    script = REMOVE_CHECKOUT + "\nremove_checkout(*sys.argv[1:])\n"
    result = subprocess.run(
        ssh_python(row["ssh_host"], script, row["remote_repo"], row["remote_scratch"], row["decision_id"]),
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode:
        raise ValueError("remote decision scratch checkout cleanup failed")


def remote_command(argv: list[str], row: dict, timeout: float) -> list[str]:
    script = (
        REMOVE_CHECKOUT
        + """brief=sys.stdin.buffer.read()
os.environ.pop("OPENAI_API_KEY",None)
os.environ.pop("CLAUDECODE",None)
repo,scratch,decision,cwd,limit=sys.argv[1:6]
try:
 p=subprocess.Popen(sys.argv[6:],cwd=cwd,stdin=subprocess.PIPE,start_new_session=True)
 try:
  p.communicate(brief,timeout=float(limit))
 except subprocess.TimeoutExpired:
  import signal
  os.killpg(p.pid,signal.SIGKILL)
  p.communicate()
  sys.exit(124)
 sys.exit(p.returncode if p.returncode >= 0 else 128-p.returncode)
finally:
 try:
  remove_checkout(repo,scratch,decision)
 except (OSError,ValueError,subprocess.SubprocessError):
  print("route-run: in-run scratch cleanup deferred to caller",file=sys.stderr)
"""
    )
    return ssh_python(
        row["ssh_host"],
        script,
        row["remote_repo"],
        row["remote_scratch"],
        row["decision_id"],
        row["execution_cwd"],
        str(timeout),
        *argv,
    )
