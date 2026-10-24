# Native Claude artifacts across models

`claude-artifact-bridge` is a stdio MCP facade over official `claude mcp serve`. It exposes only Artifact, ArtifactComments and ArtifactData. Inference can use GPT, Kimi or Claude independently. The native owner host must already be signed into Claude; credentials are not copied to clients.

The operator designates one account in `~/.agent-lb/state/artifact-owner.json`:

```json
{"account_id":"<Agent LB Anthropic account id>","provider":"anthropic","email":"<owner email>","host":"studio"}
```

Keep this local file mode 600. Optional `config_dir` selects a separate native Claude login directory. The bridge verifies native login email and provider at startup, removes inherited gateway credentials and fails closed on mismatch. Account id records the Agent LB designation; native authentication is verified by email, not by assuming its account identifiers equal Agent LB ids.

Install with `scripts/install-claude-clients.sh --clients-only`. Run `claude-artifact-bridge --install-host` once from the logged-in Studio user session to install its private Unix-socket LaunchAgent. SSH sessions may not access the macOS native login directly. Add the installed bridge as a user-scoped Claude MCP server. Remote clients use SSH stdio to the owner host, never their own account or a copied token:

```sh
claude mcp add --transport stdio --scope user claude_artifacts -- /Users/aneyman/.local/bin/claude-artifact-bridge
claude mcp add --transport stdio --scope user claude_artifacts -- /usr/bin/ssh -T studio /Users/aneyman/.local/bin/claude-artifact-bridge --connect /Users/aneyman/.agent-lb/artifact-host/native.sock
```

Use the first command on Studio and the second on Book. New sessions load the server. Tool search may defer its schemas until needed. Explicit `--strict-mcp-config` sessions must include this server themselves. Sandboxed fleet seats need an authorized route to the owner host; user-level configuration is not implicitly copied into those sandboxes.

Remote publish paths are paths on Studio, not the calling computer. Stage a generated page and supporting files on Studio over the existing authorized SSH/SCP connection, then pass that Studio `file_path` to Artifact. Do not pass a Book-local path or copy native account credentials.

Private artifact publish and read, filtered MCP list/read, and real GPT tool invocation were verified. This does not enable the native Desktop Artifact pane in gateway mode; the artifact remains a real Claude artifact at its returned claude.ai URL. Sharing and deletion remain explicit tool operations.
