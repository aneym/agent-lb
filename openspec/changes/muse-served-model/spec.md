# Verified Muse routing

The Muse Claude bridge SHALL reject an upstream response with a missing or non-Muse model and SHALL expose the upstream model in its response.
The Muse print launcher SHALL resolve muse-latest from ROUTE_TABLE, the managed routing table, or the bundled routing table, in that order, and SHALL fail closed if no valid non-contributor Muse pin is available. The canonical muse-latest pin SHALL be muse-wonder-full-1.0. The launcher SHALL pass the resolved model to Claude Code, including when --model muse-latest is supplied, use agent-lb's Muse Claude endpoint, print the served model from the response, and exit nonzero without returning an answer when that model is missing or differs from the resolved pin.
The status CLI installer SHALL refresh managed entrypoints to the installed runtime's venv executable, never PATH Python.
Routing audits SHALL accept minute windows, including `--window 30m`.
