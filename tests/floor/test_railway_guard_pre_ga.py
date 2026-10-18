import json

import pytest

from tests.floor.railway_guard_support import (
    invoke,
)


@pytest.mark.xfail(strict=True, reason="Accepted deliberate shell obfuscation residual")
@pytest.mark.parametrize(
    "command,tokens",
    [
        ("rail''way down", {}),
        ("rail\\way down", {}),
        ("bash -lc 'unset RAILWAY_TOKEN; railway down'", {"RAILWAY_TOKEN": "synthetic"}),
    ],
)
def test_deliberate_obfuscation_residuals(command, tokens):
    assert invoke(command, **tokens).returncode == 2


@pytest.mark.parametrize(
    "command,expected",
    [
        ("railway variables --set FOO=bar", 0),
        ("railway variables -s FOO=bar", 0),
        ("railway variables -sFOO=bar", 0),
        ("railway variables -sAPI_TOKEN=synthetic", 0),
        ("railway variable set FOO bar --environment test", 0),
        ("railway variables delete FOO -e dev", 0),
        ('env sh -c "railway variables --set FOO=bar"', 0),
        ('eval "railway variables --set FOO=bar"', 0),
        ('env -S "railway variables --set FOO=bar"', 0),
        ("railway variables --set FOO=bar --environment production", 0),
        ("railway variables --set FOO=bar -e prod", 0),
        ("railway variables delete FOO --environment=PRODUCTION", 0),
        ("railway variables --set FOO=bar -eprod", 0),
        ("railway -e production variables --set FOO=bar", 0),
        ("RAILWAY_TOKEN=x railway variables --set FOO=bar -e prod", 0),
        ("railway variables --set API_TOKEN=synthetic -e dev", 0),
        ("railway variables -s API_TOKEN synthetic", 0),
        ('eval "railway variables --set API_TOKEN=synthetic"', 0),
        ('env -S "railway variables --set API_TOKEN=synthetic"', 0),
        ("railway variables", 0),
        ("railway variables --json", 0),
        ("railway variables get FOO", 0),
        ("RAILWAY_TOKEN=x railway variables get FOO", 0),
        ("railway variables --set FOO=bar --json", 0),
        ("railway up --environment dev", 0),
    ],
)
def test_pre_ga_variable_policy(command, expected):
    result = invoke(command)
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr
    assert "synthetic" not in result.stderr
    assert "bar" not in result.stderr


def test_unknown_link_write_is_logged_without_values():
    result = invoke("railway variables --set FOO=bar")
    assert result.returncode == 0, result.stderr
    assert "unknown linked environment" in result.stderr
    assert "FOO" not in result.stderr
    assert "bar" not in result.stderr


@pytest.mark.parametrize(
    "command,expected",
    [
        *[
            (f"{prefix} {args}{suffix}", 0)
            for prefix, suffix in [("railway", ""), ('eval "railway', '"'), ("npx @railway/cli", "")]
            for args in [
                "variables -s api",
                "variables -sapi",
                "vars -s api",
                "variables --service delete",
                "variables --service set",
                "variables --service remove",
            ]
        ],
        ("railway variable --service api set FOO bar", 0),
        ("railway variables --set-from-stdin", 0),
        ("railway vars --set-from-stdin -e dev", 0),
        ("railway variables --set-from-stdin -e production", 0),
        ("RAILWAY_TOKEN=x railway variables --set-from-stdin -e production", 0),
        ("railway --help run env", 0),
        ("railway -h run env", 0),
        ("railway run --help env", 0),
        ("railway run -s api -h env", 0),
        ("railway run env -h", 0),
        ("railway run printenv --help", 0),
        ("railway run sh -c env -h", 0),
        ("railway run sh -c printenv --help", 0),
        ("railway run -- env -h", 0),
        ("railway variables -s api --help", 0),
        ("railway up --help", 0),
        ('railway variables --set NAME="$TOKEN"', 0),
        ('railway variables --set NAME="${SECRET}"', 0),
        ('railway variables --set NAME="$KEY"', 0),
        ('railway variables --set NAME="$PASSWORD"', 0),
        ('railway variables --set NAME="$PORT"', 0),
    ],
)
def test_pre_ga_r2_argv_ownership(command, expected):
    result = invoke(command)
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr


@pytest.mark.parametrize(
    "command,expected",
    [
        ("railway ssh --service rails --environment staging -- python -c ...", 0),
        ("railway ssh --service rails --environment staging -- python -c 'print(1)'", 0),
        ("railway ssh --service rails --environment production -- python -c 'print(1)'", 0),
        ("railway ssh -e production -- printenv", 0),
        ("railway run -e dev -- pytest", 0),
        ('railway ssh -e preview -- python -c "print(1)"', 0),
        ("railway -e test run -- pytest", 0),
        ("railway ssh --environment=feature-one -- script.py", 0),
        ("railway run -edev -- pytest", 0),
        ("railway ssh -e production -- python -c ...", 0),
        ("railway run -e prod -- pytest", 0),
        ("railway ssh -- python -c ...", 0),
        ("railway run -- pytest", 0),
        ("RAILWAY_TOKEN=x railway ssh -e production -- python -c ...", 0),
        ("RAILWAY_TOKEN=x railway run -e prod -- pytest", 0),
        *[
            (f"railway {mode} -e staging -- {output}", 0)
            for mode in ["ssh", "run"]
            for output in [
                "printenv",
                "env",
                "set",
                "export -p",
                "railway variables",
                "railway variables --json",
                'echo "$API_TOKEN"',
                'printf "%s" "${PASSWORD}"',
                "cat /app/.env",
                "cat .env.staging",
                'sh -c "printenv"',
                'sh -c "echo okay; env"',
            ]
        ],
        ('railway ssh -e staging -- echo "$PORT"', 0),
        ("railway run -e dev -- cat README.md", 0),
        ("railway ssh -e staging -- echo sk-synthetic", 0),
        ("railway run -e dev -- pytest API_TOKEN=synthetic", 0),
    ],
)
def test_pre_ga_ssh_run_policy(command, expected, tmp_path):
    result = invoke(command, HOME=str(tmp_path))
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr
    assert "synthetic" not in result.stderr


@pytest.mark.parametrize("mode", ["ssh", "run"])
@pytest.mark.parametrize(
    "environment,expected",
    [
        ("staging", 0),
        ("dev", 0),
        ("production", 0),
        ("PROD", 0),
        (None, 0),
    ],
)
def test_pre_ga_ssh_run_linked_environment(mode, environment, expected, tmp_path):
    home = tmp_path / "home"
    config = home / ".railway" / "config.json"
    config.parent.mkdir(parents=True)
    project = tmp_path / "project"
    child = project / "child"
    child.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "projects": {
                    str(project): {
                        "project_path": str(project),
                        "project": "synthetic-project",
                        "environment": "synthetic-environment",
                        "environment_name": environment,
                    }
                }
            }
        )
    )
    result = invoke(f"railway {mode} -- python -c ...", cwd=child, HOME=str(home))
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr

    result = invoke(f"railway {mode} -e staging -- python -c ...", cwd=child, HOME=str(home))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command,expected",
    [
        ("npx @railway/cli@latest variables", 0),
        ("npx @railway/cli@1.2.3 up", 0),
        ("RAILWAY_TOKEN=x npx @railway/cli@1.2.3 up", 0),
        ("railway variables -e dev --set TASK_ID=3", 0),
        ("railway variables -e dev --set DISK_SIZE=10", 0),
        ("railway variables -e dev -s task-api --set PORT=8080", 0),
        ("railway variables -e dev --set VALUE=sk-synthetic", 0),
        *[
            (f"railway {mode} -e production -- {output}", 0)
            for mode in ["ssh", "run"]
            for output in [
                "time env",
                "sudo env",
                "nice env",
                "command env",
                "sudo -n -u user env",
                "nice -n 5 printenv",
                'sh -c "true; env"',
                'sh -c "FOO=1 env"',
                'sh -c "echo hi && env"',
            ]
        ],
        ("railway variables --set X=$(agent-secret get synthetic)", 0),
        ("railway variables --set X=$(echo secret)", 0),
        ('railway variables --set X="$(echo secret)"', 0),
        ("railway variables --set X=$(cat input)", 0),
        ("railway variables --set PORT=8080 -ks api", 0),
        ("railway variables --set PORT=8080 -sk", 0),
    ],
)
def test_pre_ga_review_advisories(command, expected):
    result = invoke(command)
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr
    assert "synthetic" not in result.stderr


@pytest.mark.xfail(strict=True, reason="Accepted arbitrary Python value-output residual")
def test_python_environment_printing_residual():
    result = invoke('railway ssh -e production -- python -c "import os; print(os.environ)"')
    assert result.returncode == 2


@pytest.mark.parametrize(
    "command,expected",
    [
        ("railway run -- npx @railway/cli variables", 0),
        ('railway run -- bash -c "railway service delete x"', 2),
        ("railway run -- sudo railway down", 2),
        ('railway ssh -- sh -c "true && railway delete"', 2),
        ("railway run -- railway up", 0),
        ("railway up", 0),
        ("railway run -- xargs printenv", 0),
        ('railway run -- eval "railway variables"', 0),
        ("railway run -- timeout 10 railway delete", 2),
        ("railway run -- exec railway delete", 2),
        ('railway run -- su -c "railway delete"', 2),
        ("railway run -- xargs railway delete", 2),
        ("railway delete", 2),
        ("railway down", 2),
        ("RAILWAY_TOKEN=x railway run -- sudo railway down", 0),
        ("railway deploy", 0),
        ("railway redeploy", 0),
        ("railway run -- railway deploy", 0),
        ("railway ssh -- railway redeploy", 0),
    ],
)
def test_ssh_run_full_nested_policy(command, expected):
    result = invoke(command)
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr
    if expected == 2 and any(word in command for word in ["delete", "down"]):
        assert "destructive data" in result.stderr


@pytest.mark.parametrize(
    "command,expected",
    [
        *[(f"railway {args}", 0) for args in ["link", "add", "volume", "service api", "environment new staging"]],
        ("railway volume delete data", 2),
        *[
            (f'railway ssh {flag} abc -- sh -c "railway delete"', 2)
            for flag in ["--deployment-instance", "-i", "--service-instance"]
        ],
        *[(f"railway run -- xargs {flag} f printenv", 0) for flag in ["-a", "-I", "-n", "-P", "-L", "-s", "-d", "-E"]],
        ("railway run -- " + "nice " * 3000 + "printenv", 0),
    ],
)
def test_r4_trim_counterexamples(command, expected):
    result = invoke(command)
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr
    if "printenv" in command:
        assert "secret floor" in result.stderr
