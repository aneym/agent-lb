## Merging, deploy and review (2026-10-08)

Until launch the main machine's checkout is the source of truth and GitHub is a mirror (Alex, 2026-10-08 08:58 ET, full approval; his words are in `WORKSPACE.md`). Transition (2026-10-08): until the Factory CoS posts the flip bulletin (`~/.agent-rails/studio-cutover/FLIPPED` exists), `python3 scripts/review_pr.py <n>` still queues a PR through GitHub and that is the merge path; from the flip on, `python3 scripts/studio_merge.py merge <branch>` is the only path and GitHub is a mirror.
- **Agent Rails merges only through `scripts/studio_merge.py merge <branch>` on the main machine:** a check under 5 s, then the merge and a mirror push; never `gh pr merge`, never push to GitHub main by hand, and never rebuild the gate in a harness.
- **On a `conflict` refusal,** rebase the branch once on main and merge again; a second conflict goes to the lane lead.
- **Railway deploys from the mirrored main on push,** ungated; a failed deploy is fixed forward.
- **No cross-vendor or post-merge review while pre-launch** (Alex, 2026-10-08 ~12:10 ET: "new rule: as long as the agent that does the work has e2e check its own work, lets not worry about codex reviews yet. as long as claude determined the right teps to take for the architecture and stuff."). It holds when the implementing agent e2e-checked its own work and Claude (Opus or Fable) set the architecture and steps.
- **The slice check is evidence the merge tool reads** (2026-10-08): a harness check record for the candidate sha, or a `check:` line in the commit body that the merge tool runs. A sentence in lane state is never evidence. The two mechanical preflights, the import check and test collection, stay on the merge path.
- **A shipped defect is fixed forward** and becomes the next slice (2026-10-08).
- **Review seats run on request only** (2026-10-08): verifier, codex-verifier and security-reviewer run only when a lead asks for one by name and logs the reason in the lane state. At GA the rotation checklist brings review back.
- **Other repos** without a merge tool land by a fast-forward push to `origin/main`.

