# Muse Wonder FULL in Claude Code

## Why
The user wants Muse Wonder FULL to drive existing Claude Code lanes. Muse exposes Responses, while Claude Code speaks Anthropic Messages.

## What Changes
Add a dedicated Muse Messages adapter using the existing Responses bridge, and a launcher pinning every model default to muse-wonder-full-1.0. The adapter sends muse-wonder-full-1.0 upstream unchanged; Meta serves it under that name, so it is never resolved through muse-latest (which pins the smaller muse-wonder-1.0). Expose it through Anthropic-format model discovery. Reuse the host Muse credential; never route to Anthropic. Prove discovery returns JSON and the real launcher returns OK.

## Impact
A new explicitly selected route and launcher; native Muse and GPT routes remain unchanged.
