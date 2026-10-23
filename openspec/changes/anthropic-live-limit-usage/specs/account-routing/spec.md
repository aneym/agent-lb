## ADDED Requirements

### Requirement: Live Anthropic quota observations
The service SHALL persist valid five-hour and seven-day account quota headers on Messages responses, including HTTP 429, with the observation timestamp and provider reset. Absent or malformed values SHALL NOT erase known usage or reset values. A rejected account window SHALL report at least 100 percent used.

#### Scenario: Usage API throttled
- WHEN Messages reports five-hour rejection and a future reset while the usage API returns 429
- THEN the dashboard and routing SHALL reflect exhausted five-hour quota and that reset, without changing authentication state

#### Scenario: Operator probe observes quota
- WHEN an operator probe receives account quota headers
- THEN the same quota observations SHALL be persisted even on HTTP 429

#### Scenario: Model-scoped rejection
- WHEN a request is rejected for a model while account windows remain allowed
- THEN the service SHALL NOT infer account-wide exhaustion or use allowed account windows as rejection resets

#### Scenario: Partial observation
- WHEN a response includes one valid quota window or omits its reset
- THEN unrelated quota windows and known reset values SHALL be retained
