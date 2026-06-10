---
name: "get-started"
description: |
  Set up agent-lb on this machine from scratch and connect the user's LLM
  accounts. Use when the user says "get started", "set me up", "onboard me",
  "install agent-lb", "set up the load balancer", or "connect my accounts".
metadata:
  author: agent-lb
  version: "1.0.0"
---

# Get Started — agent-lb onboarding

Follow `GETTING-STARTED.md` at the repo root, top to bottom. It is the single
source of truth for this walkthrough; this skill only pins the interaction
contract:

1. **Detect and skip.** Check `curl -fsS http://127.0.0.1:2455/health` and the
   existing `~/Library/LaunchAgents/com.agent-lb.plist` before installing
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
   billing to per-token API billing. Base URL only.
5. **Show before you touch dotfiles.** Print the exact block you want to add
   to `~/.zshrc` or `~/.codex/config.toml` and get a yes first.
6. Finish with the step-7 verification (accounts list + one real request
   through the proxy) and a short status summary.
