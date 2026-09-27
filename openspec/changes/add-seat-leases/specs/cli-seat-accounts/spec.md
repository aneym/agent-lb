## ADDED Requirements

### Requirement: Accounts are leased per attempt
The seat CLI SHALL choose leasable accounts by recent use and active leases.

#### Scenario: Least-used ready account
- **WHEN** a lease is requested with multiple ready leasable accounts
- **THEN** the least-used account is leased

#### Scenario: A second lease
- **WHEN** an account has an active lease and another is idle
- **THEN** the next lease chooses the other account

#### Scenario: Login account
- **WHEN** the only Cursor account is a keychain login
- **THEN** no_account names that account and no lease is created

### Requirement: A lease's secret stays in its bundle
The seat CLI SHALL put credentials only in the private bundle, never in lease metadata, state, ledger or outputs.

#### Scenario: Cursor bundle
- **WHEN** an API-key Cursor account is leased
- **THEN** its key appears in the bundle's env file but not in output, state, ledger or lease.json

#### Scenario: Failed log and probe
- **WHEN** a release reads a failed log and probe response containing a credential
- **THEN** no state, ledger or output contains the credential

### Requirement: A release applies the attempt's outcome
The seat CLI SHALL record a release once and update account health according to its outcome.

#### Scenario: Limit
- **WHEN** a release log reports a usage limit
- **THEN** the account cools down

#### Scenario: Confirmed auth rejection
- **WHEN** a release reports auth failure and an auth-only probe explicitly rejects the account
- **THEN** auth_ok becomes false

#### Scenario: Unconfirmed auth rejection
- **WHEN** an auth-only probe succeeds or responds inconclusively with a limit
- **THEN** the failed run does not mark auth_ok false

#### Scenario: Repeated release
- **WHEN** a released lease is released again
- **THEN** no state or ledger entry changes

#### Scenario: Unknown lease
- **WHEN** an unknown lease is released
- **THEN** the command exits 2

### Requirement: Expired leases stop counting
The seat CLI SHALL count only active leases as use.

#### Scenario: Expired lease
- **WHEN** a lease's expires_at is in the past
- **THEN** it no longer contributes to the account's use

### Requirement: State writers keep each other's changes
The seat CLI SHALL merge each writer's changes into the latest state under a lock.

#### Scenario: Concurrent run, lease and add
- **WHEN** a lease is made and an account is added during a long run
- **THEN** both changes remain after the run finishes
