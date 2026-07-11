## 1. Timeout Policies

- [x] 1.1 Separate health-probe and dashboard-read timeout policy values by locality.
- [x] 1.2 Configure `healthSession` and the dashboard session from their respective policies.

## 2. Regression Coverage

- [x] 2.1 Add focused Swift tests for loopback health 3/5 and remote health plus local/remote dashboard 15/20.
- [x] 2.2 Run focused and full macOS menu-bar Swift tests.

## 3. Specification Validation

- [x] 3.1 Validate the change strictly and validate the full OpenSpec specification set.

## 4. Deployment Verification

- [ ] 4.1 Rebuild and relaunch the menu-bar client on Studio and MacBook.
- [ ] 4.2 Verify cold remote and local refreshes load accounts, summary, and recent data without retry rows.
