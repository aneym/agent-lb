# Tasks

- [x] Update proxy API-key bypass logic to use trusted-proxy-aware client IP
  resolution.
- [x] Add unit coverage for direct clients, trusted-proxy XFF resolution, and
  spoofed forwarded headers.
- [x] Add integration coverage for Funnel-shaped requests through protected
  proxy routes.
- [x] Move the public Funnel operating runbook into OpenSpec change context.
- [x] Validate OpenSpec change locally.
  - `npx --yes @fission-ai/openspec@latest validate harden-trusted-proxy-api-key-auth --strict`
    passed.
