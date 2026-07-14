## 2026-07-14 live verification

- Normal `cc` selected `fable-5/top-thinking`, then the load balancer denied the request because every account in that quota pool was cooling down until `2026-07-14T18:10:00-04:00`. Plain-Claude fallback also returned the current Fable limit. Session: `a32fba2b-1509-4edb-99ef-df9d13e8f73f`.
- Fresh `ccdex` returned `CCDEX_MODE_OK`; its result identified `gpt-5.6-sol`. Session: `b5376101-36c7-491c-a1da-5bd9364c8a26`.
- The installed worker transport returned `WORKER_MODE_OK`; its init and assistant events both identified `gpt-5.6-sol`. Job: `358051fb-6663-4083-ba35-37816b039fda`; session: `4a7e11d1-2b10-4880-be49-da03d2a4395e`; artifact: `~/.agent-lb/ccdex-jobs/358051fb-6663-4083-ba35-37816b039fda/turn-1.stdout.jsonl`.
- The machine verifier confirmed the canonical policy, both host adapters, Fable settings default, launcher tests, CCDEX hook denial, exact client links, and connected `ccdex-worker` MCP registration.
