# Anthropic routing uses the soonest-ending week first

On 2026-11-10 account 86fe250c had a full week reset with about 90 points left before its window ended the next day, and neither the ax42 primary nor the ax102 edge sent it a request for three hours. Both processes had moved it to the draining tier at 90% weekly use. After the reset it went to probing, and probing needs three successful requests, but the selector only tries probing accounts when no account is healthy. Only the follower restarts at 16:13Z and 16:15Z cleared it.

- A drained or probing account with no outstanding errors becomes healthy again as soon as its usage falls under the drain lines.
- Anthropic selection prefers the usable account whose weekly window ends soonest (day buckets, then the least weekly use), so points that would expire are used first. Every eligibility guard stays: the extra-usage veto, unusable and stale skips, the 95% line and sticky sessions.
