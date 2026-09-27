## 1. State lock and merged writes

- [x] 1.1 Lock state updates and reread the registry inside the lock.
- [x] 1.2 Merge run, probe and add changes into fresh state.

## 2. Lease

- [x] 2.1 Pick a leasable account, create the bundle and record the lease.

## 3. Release

- [x] 3.1 Classify outcomes and confirm auth failures with a separate probe.
- [x] 3.2 Update account health, runs and ledger without storing raw diagnostics.

## 4. Accounts view

- [x] 4.1 Include active lease counts and next expiry.

## 5. Tests and validation

- [x] 5.1 Exercise leases, merged writers, release and secret containment.
- [x] 5.2 Run strict OpenSpec, lint, integration and Python 3.9 checks.

## 6. Deploy

- [ ] 6.1 Install on every machine, enable the API-key Cursor account and smoke both vendors.
- [ ] 6.2 Archive after deployment evidence.
