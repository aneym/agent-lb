## Implementation

- [x] Add the aliases, the `readmitted` map and the two seats to the routing table.
- [x] Resolve readmitted aliases past their own retired patterns only; exempt only the named seat in the seat guard.
- [x] Resolve `astra-latest` in the ccgpt bridge.
- [x] Cover alias resolution, seat guard allow and deny, and the bridge alias at their CLI and HTTP boundaries.
- [ ] After reviewed landing, install-policy on Studio, restart the LB, and confirm ax42's coding-agents-sync applies the commit.
