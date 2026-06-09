## Why

Interactive and rootless containers (e.g. `podman run …`, devcontainers, `docker run -it`) set `/.containerenv` or `/.dockerenv`, which triggers `_in_container()` and forces the data directory to `/var/lib/agent-lb`. That path is typically not writable for a non-root user, causing a permission error on startup.

Additionally, there is no environment variable to override the default data directory, making it impossible to redirect agent-lb to a writable path without restructuring the container image.

## What Changes

- Introduce `AGENT_LB_DATA_DIR` as the highest-priority override for the default data directory, enabling operators to point agent-lb at any writable path.
- When no override is set, prefer an existing `$HOME/.agent-lb` directory even inside a container, preserving backward compatibility and avoiding permission errors when the home directory is already mounted.
- Only fall back to `/var/lib/agent-lb` when running in a container **and** `$HOME/.agent-lb` does not already exist.

## Impact

- Interactive container workflows no longer fail with permission errors when `/var/lib/agent-lb` is read-only or unwritable.
- Operators can redirect the data directory via an environment variable without bind-mounting `~/.agent-lb` into the exact same path inside the container.
