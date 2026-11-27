## Why

Account recovery should keep working when the dashboard restarts or a desk machine is offline. Reconnecting a pooled account must never open a personal browser profile or replace a different account's credentials.

## Change

- Run account recovery on the federation owner with a durable, identity-bound OAuth flow.
- Verify recovered credentials before clearing the recovery request and notifying follower instances.
- Use an isolated Linux display for each interactive browser profile, with a device-code path that needs no server browser.
