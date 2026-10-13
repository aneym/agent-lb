# Partial implementation receipt, 2026-10-13

Author vendor: OpenAI. The overall integration is blocked. This receipt proves the offline resolver and provider guard only.

## Check

```sh
MUSE_CHECK_HOME=$(mktemp -d /tmp/muse-check-home.XXXXXX)
HOME="$MUSE_CHECK_HOME" .venv/bin/python -m pytest -q tests/integration/test_muse_code_bootstrap.py tests/test_provider_cli_guard.py tests/unit/test_route_cli.py
```

The executed command used `/tmp/muse-check-home` as the scratch HOME. Exit 0, last lines:

```text
........................................................................ [ 62%]
............................................                             [100%]
116 passed in 72.56s (0:01:12)
```

The initial run failed during conftest startup because app startup opened `/home/jobs/.agent-lb/forensics/py-stacks.log` outside the writable roots. Redirecting HOME allowed the same tests to run without modifying production or test startup.

## Additional validation

The cached OpenSpec CLI is `/home/jobs/.npm/_npx/20f2b75ddc8bce88/node_modules/@fission-ai/openspec/bin/openspec.js`.

```text
openspec validate add-muse-code-seat --strict
Change 'add-muse-code-seat' is valid

openspec validate --specs
Totals: 40 passed, 0 failed (40 items)
```

Both exited 0. The repository specs have existing warnings about long requirements. Artifact instruction generation also warns about an existing unknown `context_docs` rule. This change itself passes strict validation.

```text
.venv/bin/python -m ruff check clients/route tests/integration/test_muse_code_bootstrap.py
All checks passed!

route resolve muse-latest --json
{"alias": "muse-latest", "model": "muse-wonder-1.0", "reason": "pinned muse-wonder; account eligibility is checked at dispatch"}
```

Resolver execution used the worktree routing table and an empty API-model fixture. The class, ladder and seats maps equal HEAD. `py_compile clients/route`, Bash syntax validation, both owner.ask argument/schema validators, the prose audit and `git diff --check` passed.

Required global lint was run and exited 1:

```text
E501 Line too long (121 > 120)
app/audit_tabs.py:122:121
Found 1 error.
```

Running Ruff on `git show HEAD:app/audit_tabs.py` reproduced that error. It is outside this change and was left intact.

## Parked

The official distribution build is proposed as `1.4.6-R6000.1`; no binary was downloaded or fingerprinted. Shell DNS refused the Meta installer and GitHub origin lookup. The named Studio open-factory checkout is absent, and no substitute checkout was edited. The local origin snapshot inspected was `5a5c771ac99ae0881ca7e8ecd983064c3e160096`.

Authentication is unverified. The credential ask is written and schema-checked but not filed. Adapter parsing, quota readback, Muse pool, available seat definition, policy installation and open-factory installer/runner remain unchecked in tasks.md. The real scratch task, returned diff and request-log receipt were not run. No restart, deploy, push or agent message occurred. A cross-vendor review was not started under the brief's prohibition on messaging other agents and its no-push constraint.
