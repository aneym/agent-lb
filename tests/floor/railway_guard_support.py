import json
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "config/coding-agents/hooks/railway-vars-guard.sh"


def invoke(command, shell=None, cwd=None, **tokens):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"RAILWAY_TOKEN", "RAILWAY_API_TOKEN", "RAILWAY_SECRET_FLOOR"}
    }
    env.update(tokens)
    return subprocess.run(
        [shell or os.environ.get("RAILWAY_TEST_SHELL", "/bin/bash"), str(HOOK)],
        input=json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
        ),
        text=True,
        capture_output=True,
        env=env,
        cwd=cwd,
    )
