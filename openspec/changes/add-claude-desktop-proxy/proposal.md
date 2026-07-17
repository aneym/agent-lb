## Why

Claude Desktop's embedded Claude Code runtime cannot be launched through the per-process `cc` wrapper, so it needs a durable local intercepting proxy. The first manual experiment used a temporary proxy port in global Claude settings; when that process stopped, it disconnected every Claude Code process that inherited the setting, demonstrating the need for an atomic, health-gated, reversible installation path.

## What Changes

- Add a parentless, fixed-port mode to the existing loopback-only Claude HTTPS interception proxy.
- Add a macOS LaunchAgent installer that starts and verifies the shared proxy before atomically configuring Claude settings.
- Preserve unrelated Claude settings, reject conflicting proxy/CA values, and restore only installer-owned values during uninstall.
- Preserve explicit launcher bypass behavior even when shared proxy settings exist.
- Route only Anthropic Messages requests to agent-lb while sending same-host OAuth,
  telemetry, worker-heartbeat, and other auxiliary traffic directly to Anthropic.
- Document the supported scope as Claude Desktop's embedded Claude Code runtime; ordinary Claude Desktop chat routing is not claimed.

## Capabilities

### New Capabilities

- `claude-desktop-proxy`: Durable installation, configuration, bypass, verification, and removal of the shared Claude Desktop Code proxy.

### Modified Capabilities

- `runtime-portability`: Extend the launcher shim contract to support a parentless fixed-port runtime without weakening per-session launcher behavior.

## Impact

Affected surfaces are `clients/claude-lb-launch`, a new macOS installer and LaunchAgent, Claude's user settings file, launcher and installer tests, and onboarding documentation. The agent-lb server API and routing semantics remain unchanged; supported GPT aliases continue to be selected by the server rather than rewritten in the client. Same-host auxiliary requests retain their caller credentials and identity but never enter the agent-lb server.
