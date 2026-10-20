import subprocess

import pytest

from tests.floor.railway_guard_support import (
    HOOK,
    invoke,
)


@pytest.mark.parametrize("command", ["railway environment delete staging", "railway service delete api"])
def test_login_only_writes_are_refused(command):
    result = invoke(command)
    assert result.returncode == 2, result.stderr
    assert "custody" in result.stderr
    assert "destructive data" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "railway up",
        "railway deploy",
        "railway redeploy",
        "env railway up",
        "sudo railway up",
        "npx railway up",
        "bunx railway up",
        'sh -c "railway up"',
        "echo okay; railway up",
        "echo okay\nrailway up",
        'echo "$(railway up)"',
        "RAILWAY_TOKEN=x echo okay; railway up",
        "RAILWAY_TOKEN=x env -u RAILWAY_TOKEN railway up",
        "RAILWAY_TOKEN=x env -i railway up",
        "RAILWAY_TOKEN=x railway up",
        "RAILWAY_API_TOKEN=x railway deploy",
        "env RAILWAY_TOKEN=x railway redeploy",
        'RAILWAY_TOKEN=x sh -c "railway up"',
        "RAILWAY_TOKEN=x railway variables --set PORT=8080",
        "railway status",
        "railway logs",
        "railway environment",
        "echo ordinary",
        "cmp config/coding-agents/hooks/railway-vars-guard.sh ~/.claude/hooks/railway-vars-guard.sh",
        "python3 -m pytest -q tests/test_railway_write_guard.py",
        "env PYTHONPATH=. python3 tests/test_railway_write_guard.py",
        "nohup cat ~/.claude/hooks/railway-vars-guard.sh",
        'git commit -m "fix railway guard overblock"',
    ],
)
def test_custody_writes_and_reads_are_allowed(command):
    result = invoke(command)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("token", ["RAILWAY_TOKEN", "RAILWAY_API_TOKEN"])
def test_exported_custody_token_allows_write(token):
    result = invoke("railway up", **{token: "synthetic"})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "RAILWAY_TOKEN=x railway variables --set API_TOKEN=synthetic",
        "RAILWAY_TOKEN=x railway variables --set=API_KEY=synthetic",
        "RAILWAY_TOKEN=x railway variables set API_SECRET=synthetic",
        "RAILWAY_TOKEN=x railway variables set API_SECRET synthetic",
        "RAILWAY_TOKEN=x railway variables --set API_KEY synthetic",
        "RAILWAY_TOKEN=x railway variables --set VALUE=sk-synthetic",
        "RAILWAY_TOKEN=x railway variables --set VALUE=abcdefghijklmnopqrstuvwxyz",
        'RAILWAY_TOKEN=x railway up --header "Bearer synthetic"',
        'env RAILWAY_TOKEN=x sh -c "railway variables --set API_TOKEN=synthetic"',
    ],
)
def test_secret_shaped_argv_warns_even_with_custody(command):
    result = invoke(command)
    assert result.returncode == 0
    assert "WARNING:" in result.stderr
    assert len(result.stderr.splitlines()) == 1
    assert "synthetic" not in result.stderr


@pytest.mark.parametrize(
    "command,denial",
    [
        ("railway variables --kv", "railway variables read prints values"),
        ("RAILWAY_TOKEN=x railway variables --kv", "railway variables read prints values"),
        ("RAILWAY_TOKEN=x railway variables -s api -k", "railway variables read prints values"),
        ('echo "$(railway variables --kv)"', "railway variables read prints values"),
        ("railway run printenv", "railway run printenv/env prints every secret"),
        ("RAILWAY_TOKEN=x railway run -s api -- env", "railway run printenv/env prints every secret"),
    ],
)
def test_reads_that_print_secret_values_warn_even_with_custody(command, denial):
    result = invoke(command)
    assert result.returncode == 0, result.stderr
    assert "WARNING:" in result.stderr
    assert denial in result.stderr
    assert "secret floor" in result.stderr


@pytest.mark.parametrize(
    "command,tokens,expected",
    [
        *[
            (f"{runner} @railway/cli up", {}, 0)
            for runner in ["npx", "bunx", "pnpm dlx", "yarn dlx", "npm exec", "npm exec --"]
        ],
        ('eval "railway up"', {}, 0),
        ('env -S "railway up"', {}, 0),
        ("RAILWAY_TOKEN=x npx @railway/cli variables --set API_TOKEN=synthetic", {}, 0),
        ('RAILWAY_TOKEN=x eval "railway variables --set API_TOKEN=synthetic"', {}, 0),
        ('RAILWAY_TOKEN=x env -S "railway variables --set API_TOKEN=synthetic"', {}, 0),
        ("echo \"$(railway up --service 'api(staging)')\"", {}, 0),
        ("echo \"`railway up --service 'api(staging)'`\"", {}, 0),
        ("railway variables < /dev/null --set PORT=8080", {}, 0),
        ("RAILWAY_TOKEN=x railway variables --set < /dev/null API_TOKEN=synthetic", {}, 0),
        ('RAILWAY_TOKEN="$MISSING" railway up', {}, 0),
        ('RAILWAY_TOKEN="" railway up', {}, 0),
        ('RAILWAY_TOKEN="$CUSTODY" railway up', {"CUSTODY": "synthetic"}, 0),
        ("env -uRAILWAY_TOKEN railway up", {"RAILWAY_TOKEN": "synthetic"}, 0),
        ("env -u RAILWAY_TOKEN railway up", {"RAILWAY_TOKEN": "synthetic"}, 0),
        ("nohup echo okay\ncat <<'EOF'\nwe use railway for deployment\nEOF\n", {}, 0),
        ('cat <<"EOF"\n$(railway up)\nEOF\n', {}, 0),
        ("cat <<EOF\nrailway up\nEOF\n", {}, 0),
        ("cat <<EOF\n$(railway up)\nEOF\n", {}, 0),
        ("printf '%s\\n' railway", {}, 0),
        ("cat /tmp/railway", {}, 0),
        ("timeout 10 railway status", {}, 0),
        ("timeout 10 railway up", {}, 0),
        ("timeout 10 railway up", {"RAILWAY_TOKEN": "synthetic"}, 0),
        *[(f"{wrapper} railway up", {}, 0) for wrapper in ["nice", "nice -n 5", "time", "exec", "command", "xargs"]],
        ("npx @railway/cli up", {"RAILWAY_TOKEN": "synthetic"}, 0),
        ('eval "railway up"', {"RAILWAY_TOKEN": "synthetic"}, 0),
        ('env -S "railway up"', {"RAILWAY_TOKEN": "synthetic"}, 0),
    ],
)
def test_review_counterexamples(command, tokens, expected):
    result = invoke(command, **tokens)
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr
    assert "synthetic" not in result.stderr


@pytest.mark.parametrize(
    "command,tokens,expected",
    [
        ("RAILWAY_TOKEN=x railway variables --set API_TOKEN 1234 > /dev/null", {}, 0),
        ("RAILWAY_TOKEN=x railway variables --set API_TOKEN 1234 2 > /dev/null", {}, 0),
        ("railway status 2>/dev/null", {}, 0),
        ("RAILWAY_TOKEN=$() railway up", {}, 0),
        ("RAILWAY_TOKEN=\"$(printf '')\" railway up", {}, 0),
        ('RAILWAY_API_TOKEN="$(unknown)" railway up', {}, 0),
        ('RAILWAY_TOKEN="$(unknown)" railway up', {"RAILWAY_TOKEN": "synthetic"}, 0),
        *[
            (f"RAILWAY_TOKEN=x sudo {options} railway variables --set API_TOKEN=synthetic", {}, 0)
            for options in ["-n", "-u USER", "-E", "--", "-n -u USER -E --", "--user USER"]
        ],
        ("sudo -n railway up", {}, 0),
        ("sudo -n railway status", {}, 0),
        ("# docs: cat <<EOF\nrailway up\n", {}, 0),
        ("echo '<<EOF'\nrailway up\n", {}, 0),
        ('echo "<<EOF"\nrailway up\n', {}, 0),
        ("cat <<EOF\n'$(railway up)'\nEOF\n", {}, 0),
        ("cat <<EOF\n'`railway up`'\nEOF\n", {}, 0),
        ("cat <<EOF\n'$(RAILWAY_TOKEN=x railway variables --set API_TOKEN=synthetic)'\nEOF\n", {}, 0),
        ("cat <<EOF\n'`RAILWAY_TOKEN=x railway variables --set API_TOKEN=synthetic`'\nEOF\n", {}, 0),
        ("cat <<'EOF'\n'$(railway up)'\nEOF\n", {}, 0),
    ],
)
def test_r3_counterexamples(command, tokens, expected):
    result = invoke(command, **tokens)
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr
    assert "synthetic" not in result.stderr


@pytest.mark.parametrize(
    "command,tokens,expected",
    [
        ("echo hi # note\nrailway up", {}, 0),
        ("echo hi # owner's note\nrailway status", {}, 0),
        ("echo hi # owner's note\nrailway up", {}, 0),
        ("echo hi # $(railway up)\nrailway status", {}, 0),
        ("echo '# literal'\nrailway up", {}, 0),
        ("cat <<< hello\nrailway up", {}, 0),
        ("cat <<< hello\nrailway status", {}, 0),
        ("cat <<< hello\nrailway up", {"RAILWAY_TOKEN": "synthetic"}, 0),
        *[
            (f"sudo {options} railway up", {}, 0)
            for options in ["-nu USER", "-Eu USER", "-nuUSER", "-EuUSER", "-nEu USER", "-ng GROUP"]
        ],
        *[(f"sudo {options} railway status", {}, 0) for options in ["-nu USER", "-Eu USER"]],
        *[
            (f"RAILWAY_TOKEN=x sudo {options} railway variables --set API_TOKEN=synthetic", {}, 0)
            for options in ["-nu USER", "-Eu USER"]
        ],
        ("sudo -nu USER railway up", {"RAILWAY_TOKEN": "synthetic"}, 0),
    ],
)
def test_r4_counterexamples(command, tokens, expected):
    result = invoke(command, **tokens)
    assert result.returncode == expected, result.stderr
    if expected == 0 and result.stderr and "allowed pre-GA" not in result.stderr:
        assert "WARNING:" in result.stderr
    assert "synthetic" not in result.stderr


def test_deep_check_exception_is_denied():
    result = invoke("eval " * 3000 + "railway delete")
    assert result.returncode == 2, result.stderr
    assert "railway guard error: RecursionError" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "railway variables",
        "railway variables --kv",
        "railway variables --json",
        "railway variables get FOO",
        "railway run printenv",
        "railway ssh env",
        "railway variables --set API_TOKEN=synthetic",
        'railway run -- sh -c "echo okay; env"',
    ],
)
def test_ga_secret_floor_restores_blocking(command):
    result = invoke(command, RAILWAY_SECRET_FLOOR="1")
    assert result.returncode == 2, result.stderr
    assert "secret floor" in result.stderr
    assert "synthetic" not in result.stderr


@pytest.mark.parametrize(
    "command,expected",
    [
        ('railway status "', 0),
        ('echo railway "', 0),
        ('railway delete "', 2),
        ('railway down "', 2),
        ('railway run -- sh -c "railway delete \\"', 2),
    ],
)
def test_unbalanced_quotes_are_advisory_except_destructive(command, expected):
    result = invoke(command)
    assert result.returncode == expected, result.stderr
    if expected == 2:
        assert "destructive data" in result.stderr


@pytest.mark.parametrize(
    "payload", ["not json", "[]", "null", "42", '{"tool_input": null}', '{"tool_input": {"command": []}}']
)
def test_malformed_non_railway_input_fails_open(payload):
    result = subprocess.run(["/bin/bash", str(HOOK)], input=payload, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("secret_floor", [None, "0", "1"])
@pytest.mark.parametrize(
    "command,expected",
    [
        ("jq() { cat; }; railway variables --json | jq keys", 2),
        ("railway variables --json | jq keys", 2),
        ("railway variables --json | /tmp/jq keys", 2),
        ("railway variables --json | ./jq keys", 2),
        ("alias jq=cat; railway variables --json | /usr/bin/jq keys", 2),
        ("other() { cat; }; railway variables --json | /usr/bin/jq keys", 2),
        ("function other { cat; }; railway variables --json | /usr/bin/jq keys", 2),
        ("enable -f /tmp/jq jq; railway variables --json | /usr/bin/jq keys", 2),
        ("hash -p /tmp/jq jq; railway variables --json | /usr/bin/jq keys", 2),
        ("builtin alias jq=cat; railway variables --json | /usr/bin/jq keys", 2),
        ("command alias jq=cat; railway variables --json | /usr/bin/jq keys", 2),
        ("railway variables --json | /opt/homebrew/bin/jq 'keys, .'", 2),
        ("railway variables --json | /usr/bin/jq keys .", 2),
        ("railway variables --json | /usr/bin/jq keys; echo done", 2),
        ("railway variables --json | /usr/bin/jq keys > names", 2),
        ("railway variables --kv", 2),
        ("railway variables --json | /opt/homebrew/bin/jq keys", 2),
        ("railway variables --json | /usr/bin/jq 'keys'", 2),
        ('railway variables --json | /usr/bin/jq "keys"', 2),
        ("/opt/homebrew/bin/jq(\n) { cat; }; railway variables --json | /opt/homebrew/bin/jq keys", 2),
        ("BASH_ENV=/tmp/x bash -c 'railway variables --json | /opt/homebrew/bin/jq keys'", 2),
        ("railway status", 0),
    ],
)
def test_names_only_pipelines_cannot_exempt_value_reads(command, expected, secret_floor):
    tokens = {} if secret_floor is None else {"RAILWAY_SECRET_FLOOR": secret_floor}
    result = invoke(command, **tokens)
    assert result.returncode == expected, result.stderr
    if expected == 2:
        assert "secret floor" in result.stderr
