# Stabilize Anthropic cache prefixes

Claude Code 2.1.280 emits a per-turn billing marker as the first system block. Its `cch` and `cc_prompt_id` vary for every request, ahead of the client's cache-control breakpoints. Move that marker after the cacheable system blocks when forwarding to Anthropic. Preserve the marker content, stable system blocks, tools, messages, and identity requirement.

Follow-up: Moving it after the system breakpoint still leaves it before all message-level breakpoints. Pin `cch` and `cc_prompt_id` to their first values for an identifiable Claude session in a bounded, idle-expiring cache; keep the rest of the marker text intact. Without a session ID, leave the marker untouched.
