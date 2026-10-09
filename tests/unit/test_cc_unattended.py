"""cc --unattended at its process boundary: the real launcher runs, and only the two programs it execs are probes.

factory-operations 103 (2026-10-09): every unattended CoS, scoping or lead launch runs under `agent-pane-policy exec`
so a permission prompt or a question never parks it; a tab Alex talks to (plain `cc`) is unchanged.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

CC = Path(__file__).resolve().parents[2] / "clients" / "cc"
PROBE = "#!/usr/bin/env python3\nimport json, os, sys\nprint(json.dumps({'name': os.path.basename(sys.argv[0]), 'argv': sys.argv[1:]}))\n"


def probe(path):
    path.write_text(PROBE)
    path.chmod(0o755)
    return path


def launch(tmp_path, args, helper=True, env_extra=None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    shutil.copy2(CC, bindir / "cc")
    opus = probe(bindir / "opus")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    if helper:
        env["AGENT_PANE_POLICY_BIN"] = str(probe(tmp_path / "agent-pane-policy"))
    env.update(env_extra or {})
    result = subprocess.run([str(bindir / "cc"), *args], capture_output=True, text=True, env=env, timeout=30)
    return result, str(opus)


def test_an_unattended_launch_runs_under_the_pane_policy(tmp_path):
    result, opus = launch(tmp_path, ["--unattended", "-p", "hi"])
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out == {"name": "agent-pane-policy", "argv": ["exec", "--", opus, "--permission-mode", "auto", "-p", "hi"]}


def test_the_environment_switch_marks_a_launch_unattended(tmp_path):
    result, opus = launch(tmp_path, ["-p", "hi"], env_extra={"AGENT_UNATTENDED": "1"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["name"] == "agent-pane-policy"


def test_a_tab_alex_talks_to_is_not_wrapped(tmp_path):
    result, opus = launch(tmp_path, ["-p", "hi"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"name": "opus", "argv": ["--permission-mode", "auto", "-p", "hi"]}


def test_an_unattended_launch_without_the_policy_fails_closed(tmp_path):
    result, _ = launch(tmp_path, ["--unattended"], helper=False)
    assert result.returncode != 0
    assert "agent-pane-policy" in result.stderr
    assert result.stdout == ""
