# Verified Muse routing

The Muse Claude bridge SHALL reject an upstream response with a missing or non-Muse model and SHALL expose the upstream model in its response.
The Muse print launcher SHALL use agent-lb's Muse Claude endpoint, print the served model from the response, and exit nonzero without returning an answer when that model is missing or not Muse.
The status CLI installer SHALL refresh managed entrypoints to the installed runtime's venv executable, never PATH Python.
Routing audits SHALL accept minute windows, including `--window 30m`.
