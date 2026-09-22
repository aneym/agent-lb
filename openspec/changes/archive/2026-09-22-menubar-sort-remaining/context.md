# Verification

- Local Swift suite: 204 tests passed, exit 0.
- Release bundle build and strict ad-hoc signature verification: exit 0.
- Strict validation of this change and macos-menubar spec: exit 0. Repository-wide spec validation remains red on unrelated existing specifications.
- Native AccountsSection rendering with isolated preferences and synthetic 40/10/9 percent accounts displays 9/10/40, despite a provider-view name-descending preference. This is local native fixture proof, not provider telemetry proof.
- Installed matching binary on Book; SHA-256: `52e19485e64526ed1865ea0e0344a1cda7097ca3b1bb2a51c9e02e156858fa03`. Native diagnostics report service running, 11 accounts, summary loaded, no section errors, and an on-screen status item. Saved connection and provider selection preserved.
- Backend routing, quotas and account records unchanged.
