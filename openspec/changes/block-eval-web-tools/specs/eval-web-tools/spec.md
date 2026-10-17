## ADDED Requirements

### Requirement: Eval web tools
Requests with API key name `of-harbor` or resolved seat starting with `eval.` SHALL omit Anthropic web_search and web_fetch tool types and OpenAI web_search tool types, including versioned variants. Empty tools SHALL be omitted; Anthropic tool_choice naming a removed tool SHALL be omitted. Removed types SHALL be logged in nullable stripped_tools JSON text. Other traffic SHALL retain its tools.

#### Scenario: Eval request
- **WHEN** eval traffic requests provider web tools
- **THEN** upstream receives only permitted tools and removed types are logged

#### Scenario: Ordinary request
- **WHEN** other traffic requests web tools
- **THEN** its tools are preserved
