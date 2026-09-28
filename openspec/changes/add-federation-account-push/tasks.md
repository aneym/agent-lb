# Tasks: add-federation-account-push

- [x] 1. Spec seat: schemas `FederationPushRequest`, `FederationPushSkip`,
      `FederationPushResponse` in `app/modules/federation/schemas.py`;
      settings `federation_push_path`, `federation_push_interval_seconds`,
      `federation_push_sources_path`, `federation_tailscale_bin`.
- [x] 2. Spec seat: scenario tests
      `tests/integration/test_federation_push_receiver.py` and
      `tests/unit/test_federation_push_sender.py`, failing before the pieces land.
- [ ] 3. Receiver piece: `app/modules/federation/push_receiver.py`
      (`run_tailscale_whois`, `parse_tailscale_whois_login`,
      `resolve_tailscale_login` with a 60 s per-IP cache, sources file with
      mtime reload, instance binding state file 0600, push apply), the
      `POST /api/federation/push` route in `api.py`, and scoped removal in
      `repository.py`.
- [ ] 4. Sender piece: `app/modules/federation/push.py` (`load_push_config`,
      `match_target_accounts`, `AiohttpPushClient`,
      `FederationPushScheduler`, `build_federation_push_scheduler`,
      `run_cli`, `__main__`) and lifespan wiring in `app/main.py`.
- [ ] 5. Review: fresh cross-vendor verifier, money-path three-lens panel.
- [ ] 6. Rollout: sources file on Studio with Nate's logins; Nate sets
      `AGENT_LB_LOCAL_INSTANCE_ID` explicitly and writes his push file.
