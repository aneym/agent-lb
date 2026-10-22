import hashlib
import json
import os
import re
import subprocess
import time

REPO = "parityrepo"
BOX = "parity-box"
OWNER_PREFIX = "export FACTORY_OWNER_PANE=none; "
SUBAGENT = {"agent_id": "hook-parity-subagent", "agent_type": "general-purpose", "session_id": "hook-parity"}
LOCAL_RUN = re.compile(r"^offload-run --class [0-9a-f]{16} --decision \S+ -- pytest -q$")
BOX_RUN = re.compile(
    r"^seat-run --exec --read-only --class [0-9a-f]{16} --decision \S+ --name offload-pytest-\$\(date \+%s\)-\$RANDOM -- pytest -q$"
)
ROUTED = re.compile(r"(?:^|\s)(?:offload-run|seat-run)\s")
DECISION = re.compile(r"--decision [0-9a-f]+")
VARIANTS = {
    "offload-local": dict(mode="auto", local=8.0, box=900.0),
    "offload-box": dict(mode="auto", local=150.0, box=20.0),
    "offload-mac-only": dict(mode="auto", local=150.0, box=20.0),
    "offload-off": dict(mode="off", local=150.0, box=20.0),
    "offload-bg": dict(mode="auto", local=240.0, box=3000.0),
    "pytest-local-not-denied": dict(mode="auto"),
}


def subagent_payload():
    body = dict(SUBAGENT, hook_event_name="PreToolUse", tool_name="Bash", tool_input={"command": "pytest -q"})
    return json.dumps(body)


def cases(bash):
    cwd = "work/" + REPO
    return [
        dict(bash("pytest -q", cwd), name="offload-local", expect="allow", setup="offload"),
        dict(bash("pytest -q", cwd), name="offload-box", expect="allow", setup="offload"),
        dict(bash("xcodebuild -scheme App test", cwd), name="offload-mac-only", expect="allow", setup="offload"),
        dict(bash("pytest -q", cwd), name="offload-off", expect="allow", setup="offload"),
        dict(bash("pytest -q", cwd), name="offload-bg", expect="allow", setup="offload", raw=subagent_payload()),
        dict(bash("pytest -q", cwd), name="pytest-local-not-denied", expect="allow", setup="offload"),
    ]


def class_id(key):
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def write_rows(root, key, rows):
    path = os.path.join(root, "history", class_id(key) + ".jsonl")
    with open(path, "a") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row, class_key=key), sort_keys=True) + "\n")


def seed(root, local, box):
    load1, cores = os.getloadavg()[0], os.cpu_count()
    now = time.time()
    studio = {"studio_load1": load1, "studio_cores": cores, "rc": 0, "status": "done"}
    rows = [
        dict(studio, at=now - 600 + i, where="local", wall_s=local * (1 + i / 50.0), input_sha="0" * 40)
        for i in range(6)
    ]
    rows += [
        dict(
            studio,
            at=now - 300 + i,
            where="box",
            box=BOX,
            box_load1=2.0,
            box_cores=16,
            stage_s=1.0,
            queue_s=1.0,
            first_output_s=1.0,
            wall_s=box * (1 + i / 50.0) + 2.0,
            input_sha="0" * 40,
        )
        for i in range(6)
    ]
    for scope in ("full", "*"):
        write_rows(root, "%s|pytest|%s" % (REPO, scope), rows)


def setup(case, run):
    variant = VARIANTS[case["name"]]
    repo = os.path.join(run, "work", REPO)
    os.makedirs(repo, exist_ok=True)
    subprocess.run(["git", "init", "-q", repo], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    root = os.path.join(run, "of", "offload")
    os.makedirs(os.path.join(root, "history"), exist_ok=True)
    os.makedirs(os.path.join(root, "decisions"), exist_ok=True)
    work = os.path.join(run, "work")
    roots = sorted({work + "/", os.path.realpath(work) + "/"})
    with open(os.path.join(run, "of", "factory.toml"), "w") as handle:
        handle.write("[offload]\nroots = %s\nmain = %s\n" % (json.dumps(roots), json.dumps(os.path.join(run, "main"))))
    with open(os.path.join(root, "mode"), "w") as handle:
        handle.write(variant["mode"] + "\n")
    node = dict(
        name=BOX,
        eligible=True,
        draining=False,
        cores=16,
        load1=2.0,
        mem_free_mb=65536,
        pending_offload=0,
        running_offload=0,
    )
    with open(os.path.join(root, "signals.json"), "w") as handle:
        json.dump({"at": time.time(), "boxes": [node]}, handle)
    if "local" in variant:
        seed(root, variant["local"], variant["box"])


def env(case, run):
    if case.get("setup") != "offload":
        return {}
    return {"OF_ROOT": os.path.join(run, "of"), "OF_FACTORY_FILE": os.path.join(run, "of", "factory.toml")}


def tab_command(updated, subagent):
    command = (updated or {}).get("command")
    if not isinstance(command, str):
        return None
    if subagent:
        return command[len(OWNER_PREFIX) :] if command.startswith(OWNER_PREFIX) else None
    return command


def problems(case, outcome):
    name, updated = case["name"], outcome["new_updated_input"]
    subagent = "raw" in case
    command = tab_command(updated, subagent)
    found = []
    if outcome["new_decision"] == "deny" or outcome["decision"] == "deny":
        found.append("denied: " + (outcome["new_block_message"] or outcome["block_message"]))
    if name in ("offload-local", "offload-bg", "pytest-local-not-denied") and not (
        command and LOCAL_RUN.match(command)
    ):
        found.append("want offload-run prefix, got %r" % command)
    if name == "offload-box" and not (command and BOX_RUN.match(command)):
        found.append("want seat-run --exec --read-only prefix, got %r" % command)
    if name in ("offload-mac-only", "offload-off") and command and ROUTED.search(command):
        found.append("want no offload rewrite, got %r" % command)
    background = bool((updated or {}).get("run_in_background"))
    if background != (name == "offload-bg"):
        found.append("run_in_background %s" % background)
    return found


def without_ids(value):
    return json.loads(DECISION.sub("--decision ID", json.dumps(value)))


def subagent_only(case, mismatch):
    old, new = without_ids(mismatch.get("old")), without_ids(mismatch.get("new"))
    if old == new:
        return True
    if "raw" not in case or mismatch.get("what") != "effect":
        return False
    updated = old.get("updatedInput") or case["input"]
    if not isinstance(updated.get("command"), str):
        return False
    old["updatedInput"] = dict(updated, command=OWNER_PREFIX + updated["command"])
    return old == new


def verdict(case, outcome):
    kept = [m for m in outcome["mismatches"] if not subagent_only(case, m)]
    if case.get("setup") == "offload":
        kept += [{"what": "offload", "problem": text} for text in problems(case, outcome)]
    return kept
