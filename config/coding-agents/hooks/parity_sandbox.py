import json
import os
import re
import shutil
import threading

PATH_REF = re.compile(r'(?:\$HOME|~)/([^\s"\'$`\\;&|<>(){}]+)')


CONFIG_COPIES = (".agent-lb/managed/coding-agents/routing-table.json", ".claude/agents", ".claude/load-governor.json")


TEST_PANE = "hp:t1"


FAKE_NPX = r"""#!/bin/sh
echo "$*" >> "$NPX_CALL_LOG"
if [ "$2" = "--find-config-path" ]; then
  d=$(pwd)
  while :; do
    for f in "$d"/.prettierrc* "$d"/prettier.config*; do [ -e "$f" ] && exit 0; done
    [ "$d" = / ] && exit 2
    d=$(dirname "$d")
  done
fi
exit 0
"""


class Sandbox(object):
    def __init__(self, root, real_home, registry, dispatcher, commands, extra_files=None, remove=()):
        self.root = root
        self.template = os.path.join(root, "template")
        self.real_home = real_home
        home = os.path.join(self.template, "home")
        os.makedirs(home)
        for command in commands:
            for rel in PATH_REF.findall(command):
                rel = rel.rstrip("/")
                source = OVERLAY.get(rel) or os.path.join(real_home, rel)
                target = os.path.join(home, rel)
                if os.path.isfile(source) and not os.path.lexists(target):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if rel in OVERLAY:
                        live = os.path.join(real_home, rel)
                        shutil.copyfile(source, target)
                        os.chmod(
                            target,
                            0o755
                            if rel.endswith(".sh")
                            else os.stat(live).st_mode & 0o777
                            if os.path.isfile(live)
                            else 0o644,
                        )
                    else:
                        os.symlink(source, target)
        for rel in CONFIG_COPIES:
            source = os.path.join(real_home, rel)
            target = os.path.join(home, rel)
            if os.path.isdir(source):
                shutil.copytree(source, target, symlinks=False, ignore=shutil.ignore_patterns("*.bak*"))
            elif os.path.isfile(source):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
        hooks_dir = os.path.join(home, ".claude", "hooks")
        os.makedirs(os.path.join(hooks_dir, "dispatch"), exist_ok=True)
        dispatcher_link = os.path.join(hooks_dir, "hook-dispatch.py")
        if os.path.lexists(dispatcher_link):
            os.unlink(dispatcher_link)
        os.symlink(os.path.abspath(dispatcher), dispatcher_link)

        names = ["registry.json"] + (["registry.%s.json" % registry["rev"]] if registry.get("rev") else [])
        for name in names:
            with open(os.path.join(hooks_dir, "dispatch", name), "w") as handle:
                json.dump(registry, handle, indent=2)
        for rel in remove:
            path = os.path.join(self.template, rel)
            if os.path.islink(path) or os.path.isfile(path):
                os.unlink(path)
        for rel, (content, mode) in (extra_files or {}).items():
            path = os.path.join(self.template, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.lexists(path):
                os.unlink(path)
            with open(path, "w") as handle:
                handle.write(content)
            os.chmod(path, mode)
        for rel in ("tmp", "work", "project", "lanes", "factory", "idle", "orch", "ledger", "skill", "bin"):
            os.makedirs(os.path.join(self.template, rel), exist_ok=True)
        with open(os.path.join(self.template, "factory", "machines.json"), "w") as handle:
            handle.write("[]\n")

        with open(os.path.join(self.template, "bin", "npx"), "w") as handle:
            handle.write(FAKE_NPX)
        os.chmod(os.path.join(self.template, "bin", "npx"), 0o755)
        self.count = 0
        self.lock = threading.Lock()

    def fresh(self):
        with self.lock:
            self.count += 1
            path = os.path.join(self.root, "run-%04d" % self.count)
        shutil.copytree(self.template, path, symlinks=True)
        return path

    def env(self, run, extra=None):
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("HERDR_", "FACTORY_", "HOOK_DISPATCH_", "HOOK_FLOOR_", "CLAUDE_ENV_FILE", "TMUX"))
        }
        env.update(
            {
                "HOME": os.path.join(run, "home"),
                "TMPDIR": os.path.join(run, "tmp") + "/",
                "PATH": os.path.join(run, "bin") + os.pathsep + os.environ.get("PATH", os.defpath),
                "AGENT_LB_URL": "http://127.0.0.1:9",
                "AUTOCLOSE_DRY_RUN": "1",
                "IDLE_REAPER_DRY_RUN": "1",
                "IDLE_AGENT_STATE_DIR": os.path.join(run, "idle"),
                "IDLE_AGENT_ORCH_DIR": os.path.join(run, "orch"),
                "LANE_BULLETIN_HOME": os.path.join(run, "lanes"),
                "FACTORY_HOME": os.path.join(run, "factory"),
                "FACTORY_MACHINES": os.path.join(run, "factory", "machines.json"),
                "FACTORY_HOST": "hookparity",
                "ROUTE_LEDGER": os.path.join(run, "ledger", "dispatch.jsonl"),
                "DISPATCH_LEDGER": os.path.join(run, "ledger", "dispatch.jsonl"),
                "SKILLSTATS_HOME": os.path.join(run, "skill"),
                "NPX_CALL_LOG": os.path.join(run, "npx-calls.log"),
                "CLAUDE_PROJECT_DIR": os.path.join(run, "project"),
                "LOAD_GOVERNOR_FAKE_LOAD": "1.0",
                "LOAD_GOVERNOR_FAKE_MEM": "10",
                "LOAD_GOVERNOR_FAKE_IDLE": "90",
                "ALEX_SAID_UNBLOCK": "",
                "LANG": os.environ.get("LANG") or "en_US.UTF-8",
            }
        )
        env.update(extra or {})
        return env


def files_of(run):
    found = set()
    skip = {os.path.join(run, "trace.jsonl"), os.path.join(run, "npx-calls.log")}
    for dirpath, dirnames, filenames in os.walk(run):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
        rel_dir = os.path.relpath(dirpath, run)
        if rel_dir.split(os.sep)[0] == "tmp" or rel_dir == os.path.join("home", ".claude", "hooks", "dispatch"):
            continue
        for name in filenames:
            path = os.path.join(dirpath, name)
            if path in skip or name.startswith("hook-dispatch-") or name.endswith(".pyc"):
                continue
            found.add(os.path.relpath(path, run))
    return found


OVERLAY = {}
