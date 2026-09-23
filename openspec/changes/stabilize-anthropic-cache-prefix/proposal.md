# Stabilize Anthropic cache prefixes

Claude Code 2.1.280 emits a per-turn billing marker as the first system block. Its `cch` and `cc_prompt_id` vary for every request, ahead of the client's cache-control breakpoints. Move that marker after the cacheable system blocks when forwarding to Anthropic. Preserve the marker content, stable system blocks, tools, messages, and identity requirement.
