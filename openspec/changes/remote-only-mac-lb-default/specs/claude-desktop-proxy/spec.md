# claude-desktop-proxy Delta

## ADDED Requirements

### Requirement: Remote-only Macs default to Studio's agent-lb

On macOS, when the local agent-lb LaunchAgent plist is absent and no explicit LB URL is configured, the launcher and the desktop-proxy installer MUST default to `https://studio.tailf266ac.ts.net:2455`, and the launcher MUST NOT probe the local candidate.

#### Scenario: Mac without a local LB

- **GIVEN** a Mac home with no `com.aneyman.agent-lb.plist` and no `CLAUDE_LB_BASE_URL`, `CLAUDE_LB_LOCAL_URL` or `CLAUDE_DESKTOP_PROXY_UPSTREAM`
- **WHEN** `claude-lb-launch` selects endpoints and the installer previews its plist
- **THEN** the only launcher candidate is `https://studio.tailf266ac.ts.net:2455` and the installer upstream is the same URL

#### Scenario: Mac with a local LB

- **GIVEN** a Mac home with `com.aneyman.agent-lb.plist`
- **WHEN** the launcher selects endpoints
- **THEN** it probes `http://127.0.0.1:2455` first as before
