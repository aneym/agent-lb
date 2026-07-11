# Tasks

## 1. Installer

- [x] 1.1 Default `NumberOfFiles` soft/hard limits (4096/8192) in the generated
      LaunchAgent plist, preserving existing limit dictionaries.
- [x] 1.2 Regression tests: fresh plist gets the defaults; customized limits
      are preserved verbatim.

## 2. Deploy

- [x] 2.1 Reinstall the MacBook plist with the limits and restart the local
      service; verify `/v1/messages` stops returning fd-exhaustion 500s.
- [x] 2.2 Confirm studio's plist already carries limits (no restart needed).
