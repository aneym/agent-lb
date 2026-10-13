The watcher calls `/api/frame/db/agent` through the local relay and holds no OAuth token. The upstream base URL follows the existing Anthropic setting, which defaults to api.anthropic.com and lets tests use a local fake server.

Frame data belongs to the selected account. A retry must retain that account. The existing token helper handles concurrent rotation and account ownership. Session affinity uses the default Claude route claim's `anthropic_top_thinking` key. The seat header remains local log metadata, as on the messages route.

No readable frame calls were found in the installed CLI wrapper or local policy mirror. The route therefore accepts the five methods permitted by the brief. A POST body is forwarded as bytes without JSON parsing or system-block changes. Automatic redirects are disabled to keep the bearer on the configured host.

The Studio service uses launchd label `com.aneyman.agent-lb`. Deployment must use `~/.agent-lb/bin/lb-restart`; this change only supplies that command and does not restart the runtime.
