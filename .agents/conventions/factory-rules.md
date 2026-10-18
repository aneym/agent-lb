# Factory rules for agent-lb (2026-09-26)

agent-lb pushes straight to `main`, so it takes the factory's review rules without a merge
queue. The routing canon (`config/coding-agents/ROUTING.md`) has the evidence; ADR-0003 in
`DECISIONS.md` records the decision.

### The money path, by owned surface

A change is on the money path when it touches any surface below, or anything else that
reads or writes a request body, a credential, account state or a quota mark. The paths are
examples of each surface, not a closed list. When in doubt, it is on the money path.

1. **Request path and payload:** `app/core/anthropic/`, `app/core/openai/`,
   `app/core/clients/`, `app/core/providers/`, `app/core/upstream_proxy/`,
   `app/core/middleware/`, `app/modules/proxy/`, `clients/claude-lb-launch`,
   `clients/codex-lb-launch`. Why: moving the billing block broke the prompt cache for 43 h
   (2026-09-21) and 12 h (2026-09-23). The launchers write aliases, and aliases pick budget
   pools (ADR-0002).
2. **Accounts, credentials and custody:** `app/modules/accounts/`, `app/modules/oauth/`,
   `app/modules/sticky_sessions/`, `app/modules/federation/` (account checkout between
   instances), `app/core/crypto.py`, `scripts/anthropic-auth.sh`, `scripts/openai-auth.sh`.
   Why: the ownership and settlement trapdoors above; a request must not cross accounts.
3. **Auth:** `app/core/auth/`, `app/modules/api_keys/`, `app/modules/dashboard_auth/`,
   `app/modules/firewall/`, and the auth and firewall middleware in `app/core/middleware/`.
   Why: it decides who may call the relay and open the dashboard.
4. **Selector and quota marks:** `app/core/balancer/`, `app/core/usage/`,
   `app/core/rate_limiter/`, `app/modules/usage/`, `app/modules/quota_planner/`,
   `app/modules/pools/`, `app/modules/account_schedule/`, `app/modules/limit_warmup/`.
   Why: they decide which account serves a request, and `route` admits seats from the same
   account marks.
5. **Migrations:** `app/db/alembic/`, `app/db/migrate.py`, `app/db/models.py`. Why: they run
   while the old code still serves, so they must be additive and single-head.
6. **lb-restart and the front:** `scripts/lb-restart`, `scripts/agent-lb-front.mjs`,
   `scripts/front-hot-swap.mjs`, `scripts/install-front.sh`, `scripts/watchdog.sh`,
   `scripts/install-service.sh`, `scripts/sync-runtime.sh`. Why: before lb-restart, restarts
   held new connections for 45 to 95 s (2026-09-25).
7. **Seat routing and the tool boundary:** `clients/route` (admission and auditor choice)
   and `config/coding-agents/hooks/seat-guard.py`. Why: admission and auditor choice decide
   who reviews whom, and the seat guard fails open, so its bugs are silent. The routing
   table and verify-routing are not on the money path; verify-routing is their check.

### Traces: every request names a person and a machine (2026-09-26)

The owner asked that every request show who made it and from which machine, once more than
one person and machine share the relay. Each request log row carries four fields: a person
handle, where the person came from, a machine handle, and where the machine came from.

- **Person** (source `member`, `owner-machine`, `internal` or `unknown`): the handle of the
  member that owns the API key. Any request without a member (keyless, or a key with no
  member) counts as the owner only when its machine is on the local owner-machine list;
  otherwise it is `unknown`. A key's own name is never taken as a person.
- **Machine:** from the client address the auth layer already resolves
  (`resolve_connection_client_ip`, the same trusted-proxy settings as the firewall), never a
  second header parser. Loopback is `local`. A tailnet address takes the node's name from the
  tailnet node cache (`tailnet-unknown` on a miss); the tailnet front overwrites the forwarded
  address, so a peer cannot choose it. Funnel traffic is `funnel`; any other address is
  `remote` and the address is not stored. A loopback request that names its machine in a
  header (an ssh tunnel) is `claimed`.
- **The relay's own work** (warmups, quota probes) is `internal`, never the owner.
- **Handles only.** The owner handle, the owner-machine list and machine aliases are local env
  settings, not part of this public repo. Emails and logins are never stored.
- Anything that reads or writes these fields touches auth and the request path, so it is on
  the money path.

### Review

Pre-launch (Alex, 2026-10-07 21:35 ET: "treat this as a personal project. we want fast
iteration and not treating it liek a prod app yet until we're launched"), review runs after
merge. The three-lens money-path panel is the GA setting.

- **One check per change.** Every change names the command that proves it. A bug fix gets its
  test at the failing product path (the trapdoor above).
- **One fresh verifier from the other vendor reads every change after push.** `verifier`
  (Sonnet, high) reviews GPT, Cursor, Devin, GLM and Kimi authors; `codex-verifier` (Sol,
  xhigh) reviews Claude authors. The brief names the author vendor. Money-path changes get the
  same one reviewer. Must-fix findings become the next change; nothing waits for them.
- **Still reviewed before push:** credential custody (crypto, OAuth and auth scripts) and
  spend-cap code. A FAIL there is fixed and re-verified, or the commit records
  `Verify-override: <reason>`.
- **Unchanged floors.** Privacy allow-lists, real outbound to a person and destructive data
  keep their rules and tests; the Anthropic cache eval still runs after every request-path
  restart or deploy (rule 4).

### The verdict record

For a change reviewed before push, stage everything, then take the diff id:
`git diff --cached | git patch-id --stable | cut -c1-12`. Hand the id to the verifier in its
brief; it echoes `VERDICT PASS|FAIL diff=<id>`. The commit records the author seat and the
verdict as trailers:

```
Seat: gpt-implementer (openai)
Verified-by: verifier (anthropic) PASS effort=high diff=3f9c1a2b7d04
```

- Vendors: `anthropic`, `openai`, `cursor`, `devin`, `glm`, `kimi`, `human`.
- Several authors: one `Seat:` line each. The verifier differs in vendor from all of them.
- A clean `git pull --rebase` keeps the id; a conflicted one needs a re-verify.

### Incidents and rule changes

- An incident fix ships its fixture test or check in the same commit. On the factory's
  2026-09-26 night watch a person, not a check, found every incident.
- Rules change with evidence. Record the change as a dated `DECISIONS.md` entry that
  supersedes the old one; history stays in git.

### Why no push check

Nothing checks the trailers; they are the record, not an enforcement. They are
self-attested, so a check could not show that a verifier read the diff. A pre-push hook
would live in the git dir that every worktree and the live main checkout share, so
installing it changes other agents' pushes. A self-attested check stops only an accidental
push. The verifier brief and this rule carry the weight. A guard may come later, when a hook
can be installed per worktree.
