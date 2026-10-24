## ADDED Requirements

### Requirement: Truthful routed identity
The relay SHALL replace only a complete generated model-identity system reminder when a non-Claude model serves a Claude client. It MUST preserve user prose, history, tools, billing marker position, existing system block order and cache metadata. It SHALL add authoritative route identity only to non-Claude inference instructions without cache metadata. It MUST name the actual serving model and distinguish the Claude client from that model without inventing a cutoff.

#### Scenario: Sol helper receives Claude identity reminder
- **WHEN** a generated model-identity reminder accompanies a routed Sol helper request
- **THEN** only that reminder is replaced with the serving Sol model identity

### Requirement: Complete tool arguments
The compatibility bridge SHALL preserve tool arguments when the upstream emits arguments only in the completed item and when collecting streaming events into a non-stream response.

#### Scenario: Completed tool call without deltas
- **WHEN** an upstream tool start is followed by a completed item containing arguments without argument deltas
- **THEN** the client receives those arguments once

### Requirement: Policy-resolved Desktop transport
The Desktop gateway SHALL resolve parent and helper aliases through the canonical route command and expose upstream identity in catalog labels. Its compatibility aliases MUST NOT change native Claude model behavior outside the gateway.

#### Scenario: Catalog request
- **WHEN** Desktop requests the gateway catalog
- **THEN** the catalog labels identify the resolved upstream model for each compatibility alias
