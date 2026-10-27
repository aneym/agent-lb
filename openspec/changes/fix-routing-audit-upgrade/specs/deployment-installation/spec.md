# deployment-installation Delta

## ADDED Requirements

### Requirement: Routing audit upgrades install required sibling modules

The routing audit installer and runtime-follow bin upgrade SHALL use one versioned manifest of the audit entrypoint and its required sibling modules. When an installed entrypoint is upgraded, runtime-follow MUST install every missing required sibling from the staged tree and MUST preserve the existing drift verdict for files already installed. A missing destination MUST NOT require a historical blob to be eligible for installation.

#### Scenario: Pre-split audit installation follows the split release

- **GIVEN** a bin directory contains the historical monolithic `routing_daily_audit.py` and none of its split sibling modules
- **WHEN** runtime-follow applies the release containing the split audit
- **THEN** it replaces the entrypoint and installs every required sibling module
- **AND** `routing_daily_audit.py --help` completes successfully using the installed files

#### Scenario: Fresh routing audit installation

- **WHEN** the routing audit installer copies the entrypoint into the local bin directory
- **THEN** it copies exactly the required siblings named by the shared manifest
- **AND** the installed entrypoint can import its sibling modules without access to the source checkout
