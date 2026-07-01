---
name: "get-started"
description: |
  Set up agent-lb on this machine from scratch and connect the user's LLM
  accounts. Use when the user says "get started", "set me up", "onboard me",
  "install agent-lb", "set up the load balancer", "connect my accounts", or
  asks to wire Claude Code, Codex, OpenCode, OpenClaw, Vercel AI SDK,
  Anthropic-compatible SDK, or SDK clients.
metadata:
  author: agent-lb
  version: "1.0.0"
---

# Get Started — agent-lb onboarding

Follow `GETTING-STARTED.md` at the repo root, top to bottom. It is the single
source of truth for this walkthrough; this skill only pins the interaction
contract:

1. **Detect and skip.** Check `curl -fsS http://127.0.0.1:2455/health` and the
   existing `~/Library/LaunchAgents/com.aneyman.agent-lb.plist` before installing
   anything. Resume from the first incomplete step.
2. **You run the commands; the human only approves OAuth in a browser.** Never
   send the human to the dashboard — every step has a CLI path.
3. **Connect accounts ONE AT A TIME.** Print each auth URL as plain text, stop
   and wait for the human (Anthropic: wait for the pasted `code#state` and
   complete promptly — codes expire; OpenAI: wait for them to approve, then
   poll status). Confirm each account is `active` before starting the next
   flow. Never fabricate or guess a code.
4. **Never set `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY`** when wiring
   Claude Code to the LB — that silently switches the human from subscription
   billing to per-token API billing. Base URL only. For Anthropic SDK examples,
   keep LB credentials in `AGENT_LB_API_KEY` and pass them explicitly from code
   (for Python, `auth_token=...`) instead of exporting first-party Anthropic
   credential env vars.
5. **Show before you touch dotfiles.** Print the exact block you want to add
   to `~/.zshrc` or `~/.codex/config.toml` and get a yes first.
6. Finish with the step-7 verification (accounts list + per-account probe +
   one real request through the relevant proxy surface; for `/v1` clients,
   discover a model from `/v1/models` first) and a short status summary. A
   probe 403 "OAuth authentication is currently not allowed" means the
   account's subscription lapsed — billing, not auth; see the "Account health
   model" section of GETTING-STARTED.md before recommending a fix.
7. For SDK/app wiring, keep guidance server-side: Anthropic-compatible SDKs use
   the root Messages API base URL, OpenAI-compatible SDKs and Vercel AI SDK use
   `/v1`, and deployed apps can use the LB through a reachable backend base URL,
   but browser-direct code and deployed loopback URLs cannot spend the user's
   local subscription accounts.
