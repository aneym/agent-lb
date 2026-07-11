## 1. Launcher

- [x] 1.1 Inject `--permission-mode bypassPermissions` in ccdex mode unless the operator passes an explicit permission option; ignore `CC_PERMISSION_MODE` in ccdex mode; name the mode in the banner.

## 2. Verification and Release

- [x] 2.1 Unit tests cover the bypass default, explicit-option override, and skip-permissions suppression; focused suite passes.
- [x] 2.2 Ruff and launcher byte-compilation pass; OpenSpec validates.
- [x] 2.3 Live proof: a running ccdex child process carries `--permission-mode bypassPermissions`.
- [x] 2.4 Commit and push to `origin/main`.
