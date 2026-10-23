## ADDED Requirements
### Requirement: Operator model aliases
The proxy SHALL resolve configured aliases exactly once before provider selection and quota admission, and SHALL retain the requested alias alongside the target model in request logs.

#### Scenario: Alias request
- **WHEN** an operator maps `work` to `claude-sonnet-4-5` and a client requests `work`
- **THEN** routing and usage use `claude-sonnet-4-5` and the log retains `work`

### Requirement: Model exclusions
The proxy SHALL hide and refuse IDs matching configured fnmatch patterns, including aliases whose targets match, with protocol-shaped errors.

#### Scenario: Excluded target
- **WHEN** a model or alias target matches an exclusion
- **THEN** discovery omits it and inference refuses it before admission

### Requirement: Alias discovery
GET /v1/models and GET /v1/models/{id} SHALL expose aliases with their target ID, subject to target permissions. Unaliased models SHALL retain existing behavior.

#### Scenario: Discover alias
- **WHEN** a permitted alias is requested through model discovery
- **THEN** its entry contains its own ID and target model ID
