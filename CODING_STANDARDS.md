# Coding standards

This file holds the judgment calls agents have gotten wrong in agent-lb, one
rule per entry, each with the evidence that earned it. A breach of an entry
marked `floor: yes` (the money path, AGENTS.md rule 8) is always a must-fix; a
breach of any other entry is a must-fix unless the reviewer says why the case
differs. Entries are added only by the closeout retro or by hand, and every
entry cites evidence that anyone can open.

### S-11 One failed probe never flips durable state
Rule: Before a failed probe changes stored state, confirm the failure with a separate call that tests only that claim, for example an auth-only call before marking an account disconnected. If the confirming call does not agree, the result is inconclusive and nothing is written.
Why: The pulse took one 401 from the Codex responses probe as a rejected credential and marked every OpenAI account reauth_required, emptying the pool while the tokens still worked.
Evidence: 2026-09-25 22:42Z incident, fixed in eba3de08: codex/responses returned 401 invalid_api_key for all three OpenAI accounts at once while /wham/usage and /codex/models still accepted the same tokens. The fix marks an OpenAI account disconnected only when an auth-only /wham/usage call also returns 401; any other answer leaves the verdict inconclusive and writes nothing.
Kind: judgment · floor: yes · Added: 2026-09-27 seed (Opus) · Seen: 1
