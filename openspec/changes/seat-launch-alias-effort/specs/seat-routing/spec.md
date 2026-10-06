## ADDED Requirements

### Requirement: Effort-suffixed family aliases resolve
The router SHALL resolve a known family alias with low, medium, high or xhigh suffix to the same model as the base alias; the HTTP bridge SHALL retain the named effort.

#### Scenario: Medium Sol implementation
- **WHEN** a seat names sol-latest-medium
- **THEN** routing resolves the newest served Sol and the bridge uses medium effort

### Requirement: Authorized implementation git actions
A GPT implementation seat SHALL honor a brief that explicitly authorizes commit and push, without granting that permission to exploration or review seats.

#### Scenario: Brief asks to commit and push
- **WHEN** an implementation brief explicitly says commit and push
- **THEN** the installed seat prompt permits those scoped actions
