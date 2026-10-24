# Muse Claude Code integration

The native provider catalog lists muse-wonder-full-1.0 as a distinct model. The adapter pins that exact model, disables contributor mode, applies proxy API-key validation, and reuses the existing Responses-to-Messages tool/event translator. It removes only the Claude tool-schema NUL regex rejected by the Meta validator; other constraints stay unchanged.

The host credential is retained in the existing 0600 Muse credential file. A native Muse request to a temporary loopback bridge populated it without exposing the token. No credentials are committed.

Proof: Claude Code session 94fccc7a-5a04-4dac-8c49-151e5483e3c7 used Muse FULL to request Bash printf MUSE_TOOL_OK, received the real tool result, and completed with MUSE_TOOL_OK. Its JSONL receipt is at /Users/aneyman/.agent-rails/lanes/muse-claude-code/evidence/cc-tools3.jsonl. This is actual upstream inference, not a native Muse substitute or a model fallback.

The launcher is installed as ~/.local/bin/muse-claude-launch. Existing --resume session arguments remain Claude Code arguments. Every default/subagent model is FULL, context is 1M, effort high.
