# Hold Claude sessions on their account until it is exhausted

A Claude prompt cache belongs to the account that wrote it. Every time the router moves a session to another account, that account re-writes the session's whole context (often 200k-900k tokens) before it can read from cache again.

From 2026-09-20 to 2026-09-25 the router moved sessions 1,025 times. 186 moves were "burn-first drain" (pull a session onto an account past the Fable weekly threshold) and 73 were "budget pressure" (move at 95% of the 5-hour window). Neither was forced: both stranded the rest of the old account's window and paid a full rewrite on the new one. Fable is retired, so the burn-first preference no longer protects anything.

Change: a pinned Claude session stays on its account until upstream rejects it (a live 429 cooldown), then fails over once to the least-used account and stays there. Burn-first preference becomes opt-in.
