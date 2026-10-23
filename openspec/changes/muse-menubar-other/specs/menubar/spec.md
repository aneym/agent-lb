## ADDED Requirements

### Requirement: Other provider grouping
The native menubar SHALL group Muse seats and accounts under Other without a dedicated Muse tab. Kimi SHALL remain under Other. All SHALL continue to include these rows and counts. Muse seat vendor identity and pool tiles SHALL remain available. Saved Muse tab selections SHALL resolve to Other.

#### Scenario: Muse seat is configured
- WHEN the API reports a Muse seat
- THEN Other SHALL count and display the seat and All SHALL retain it

#### Scenario: Saved Muse scope
- WHEN the stored provider selection is Muse
- THEN the menu SHALL select Other without deleting preferences

### Requirement: Unknown Kimi quota
Kimi account rows SHALL identify quota telemetry as not reported instead of displaying a generic primary-window placeholder as five-hour or weekly capacity.

#### Scenario: Generic Kimi account usage placeholder
- WHEN the API reports primary 100 percent remaining but no provider quota windows
- THEN the Kimi row SHALL show unknown quota without claiming capacity recovery
