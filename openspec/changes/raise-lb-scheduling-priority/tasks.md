# Tasks

## 1. LaunchAgent installers

- [x] 1.1 Default service `ProcessType` to `Interactive` and `Nice` to `-10`, preserving existing typed operator overrides.
- [x] 1.2 Set the same scheduling priority for the TCP front and Claude Desktop proxy installers.
- [x] 1.3 Test fresh and customized service plists and update the existing desktop-proxy plist test.

## 2. Menubar

- [ ] 2.1 Implement the 45-second health-check grace window in the menubar client with a focused regression test (separate implementation).

## 3. Verification

- [x] 3.1 Run the focused service installer test, print and inspect a fresh service plist, validate OpenSpec, and run Python lint.
