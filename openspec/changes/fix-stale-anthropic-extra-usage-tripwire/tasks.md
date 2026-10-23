## 1. OpenSpec artifacts

- [x] 1.1 Create proposal, design, and account-routing delta spec for the stale extra-usage tripwire.

## 2. Implementation

- [x] 2.1 Honor the extra-usage tripwire in `_provider_quota_eligibility` only while the account's primary window is actively exhausted.
- [x] 2.2 Apply the same rule in the transient requested-quota cooldown bypass.

## 3. Verification

- [ ] 3.1 Regression (task acceptance suite): an account with an active tripwire cooldown and primary headroom is eligible for standard routing.
- [ ] 3.2 Regression (task acceptance suite): an account with an active tripwire cooldown and an exhausted primary window stays blocked.
- [ ] 3.3 Run `openspec validate --specs` and the task acceptance check.
