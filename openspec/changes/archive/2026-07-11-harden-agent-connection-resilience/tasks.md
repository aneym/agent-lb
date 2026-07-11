# Tasks

## 1. Shim retry budget

- [x] 1.1 Move shim connect retry defaults to module constants and raise the
      default budget to ~120s (32 attempts, 0.5s backoff, 4s cap)
- [x] 1.2 Add a regression test asserting the default budget outlasts a
      watchdog recovery (>= 100s)

## 2. Server websocket pong deadline

- [x] 2.1 Pass `ws_ping_interval=20.0, ws_ping_timeout=None` to uvicorn in
      `app/cli.py`
- [x] 2.2 Add a regression test asserting the uvicorn kwargs

## 3. Deployment & validation

- [x] 3.1 Local gate: py_compile, ruff, unit tests
- [x] 3.2 Deploy to studio (working-tree hunks + `launchctl kickstart -k`),
      confirm health and no new `1011 keepalive ping timeout` errors
