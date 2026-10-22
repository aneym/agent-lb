## 1. Research and provisioning

- [x] 1.1 Identify the official Muse CLI, distribution pin, authentication and execution contract with dated sources.
- [x] 1.2 Record the box-only credential ask; Studio login is owner-confirmed and needs no ask.
- [x] 1.3 Download and fingerprint the pinned binary through the open-factory tools installer on the authorized host.
- [x] 1.4 Confirm subscription credential provisioning and Standard mode without Alex sign-in by an agent. (Studio login by Alex 2026-10-13; contributor mode off 2026-10-16, bulletin b-20261016135734.)

## 2. Offline agent-lb wiring

- [x] 2.1 Add the pinned `muse-latest` resolver; verify the CLI returns `muse-wonder-1.0` and ladder maps match HEAD.
- [x] 2.2 Add the Muse provider CLI guard entry; verify permitted and refused commands through the subprocess tests.
- [x] 2.3 Run the focused checks, lint and OpenSpec validation; retain the receipt and commit without pushing. Global lint has the recorded pre-existing E501.

## 3. Integration requiring the native binary

- [x] 3.1 Capture safe auth, model, usage and limit readback and a redacted headless JSONL transcript.
- [x] 3.2 Implement the provider adapter, account selection, usage settlement and Muse pool using the Cursor and Devin precedents.
- [x] 3.3 Add the muse-seat forwarder and policy installation entry, then list it as available in ROUTING.md after the adapter works.
- [x] 3.4 Add the pinned durable installer and seat-exec support in the authorized open-factory worktree.
- [x] 3.5 Run route pools, resolve the pin, and execute the one-line scratch git task through seat-run with returned diff and agent-lb log evidence.
- [x] 3.6 Run all affected tests and commit both worktrees with the brief's trailers. Keep the default ladders unchanged and do not push.

## 4. Studio completion

- [x] 4.1 Transfer prepared commits to the named Studio worktrees and install policy.
- [x] 4.2 Register Studio Muse, run the scratch task through seat-run, retain the real diff and LB log receipt, and capture JSONL usage.
- [x] 4.3 Create Raise (Muse) beside Raise with the same brief, shared memory, MCP servers and tools; add its tab to pin-keep.txt. Dropped: Alex ruled Raise (Muse) moot on 2026-10-16.
- [x] 4.4 Capture a Raise status answer and live MCP tool list; record the tab ID. Dropped with 4.3.

On 2026-10-13 the adapter and factory launcher passed subprocess checks with a
vendor stand-in. Seat definitions, policy entries and installer are prepared;
3.3 and 3.4 remain unchecked until they are validated in the named Studio
worktree. 3.6 remains unchecked because HTTP fixture setup times out and global
lint has a pre-existing failure. Both local commits preserve the work without
claiming runtime readiness. Review is pending under the no-message/no-push brief.

## Closeout, 2026-10-22

Tasks 1.3 to 4.2 landed on main after the note above: Studio install and pin (3c945e25, c2601f62), seat
forwarders and ROUTING available-only entry (6e54dae0, b7447a4e), the `/muse-api` passthrough (78d864aa,
14c0bb22) and request-log attribution (61e2f5a4, a6b3ef2b). Real `muse-seat-local` runs are logged ok in
agent-lb request logs on 2026-10-22. Usage frames are absent from Muse exec output (747feb09), so the
pool reports observed runs and tokens only. Current status: `docs/muse-code.md`, section "Status, 2026-10-22".
