# Tasks

- [x] Add `SingleInstanceGuard` (flock, retained fd, bounded retry, test release
      hook) and acquire it first in `AgentLBApp.init()`; duplicate exits 0 with a
      stderr message.
- [x] Tests: fresh acquire, conflict while held, reacquire after release,
      missing-directory creation (`swift test`).
- [x] OpenSpec delta (`macos-menubar-client`).
- [x] Rebuild both machines; live-verify a second launch exits immediately while
      the first instance keeps running.
