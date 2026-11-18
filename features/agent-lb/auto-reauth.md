---
id: auto-reauth
product: agent-lb
owner: factory
status: live
covers: []
paths: ["app/reauth_auto.py", "app/reauth_auto_cli.py", "app/reauth_auto_job.py", "app/reauth_page.py", "app/reauth_enroll.py", "app/reauth_ask.py", "app/reauth_keepalive.py", "app/core/profile_lock.py", "app/lb_service.py", "scripts/install-reauth-auto.sh", "docs/auto-reauth.md", "features/agent-lb/auto-reauth.md"]
proofs: []
---
# Auto-reauth

## Sub-features
`agent-lb reauth-auto run` reauths enrolled Anthropic accounts in their own Chrome profiles with three allowed clicks, pages through one Factory owner ask per account (at most daily, never again for a cause the owner cleared) when a person is needed, and visits each profile weekly to catch lapsed sessions early.

## How to get to it
`agent-lb reauth-auto --help`; enroll with `agent-lb reauth-auto enroll <account id>`; install the job with `scripts/install-reauth-auto.sh`.

## Driving it
`agent-lb reauth-auto run --dry-run` shows what a tick would do; `agent-lb reauth <email> --auto` runs one account. See `docs/auto-reauth.md`.

## Gotchas
Until the serving agent-lb echoes `expectedAccountId` from `/api/oauth/start`, runs end `bind_not_live` with no Authorize click.
