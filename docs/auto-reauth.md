# Auto-reauth

When an enrolled Anthropic account goes `reauth_required`, `agent-lb reauth-auto run` (launchd `com.aneyman.agent-lb-reauth-auto` on Studio, every 10 minutes) signs it in again in the account's own Chrome profile, reusing the Google and claude.ai session that profile already has. A person hears about it only when the flow needs something only they can give.

## Who it acts on
- Only accounts enrolled by account id: `agent-lb reauth-auto enroll <id or prefix>`, `unenroll`, `list`. The list is `~/.agent-lb/state/auto-reauth-enrolled.json`.
- Never an account with `ownerInstance` set, never an openai account, and never the three account-id prefixes refused in `app/reauth_enroll.py`. Those log `skipped`.
- An enrolled account with no browser profile on the host logs `no_profile` and pages nobody.

## One run
1. `POST /api/oauth/start` with `expectedAccountId`. A server that binds flows echoes it back. The server then saves tokens only for that identity, on the browser callback and on the pasted code alike, and only into that row.
2. If the echo is missing (a server older than the bind), the run is `bind_not_live`: it reads the pages and checks the identity but never clicks Authorize and never sends a callback.
3. The flow opens in the profile's Chrome. Every second each frame is read. Three clicks are allowed, each a real mouse click after a fresh check for stuck markers in every frame: the Google account tile for this exact email, "Continue with Google" on the claude.ai login page (only for profiles added with `--login google`), and Claude's Authorize button after `/api/account` on the consent page shows this exact email.
4. Anything else stops the run as `stuck:<kind>`: password, code, passkey, captcha, "verify it's you", emailed sign-in code, signed out, wrong account, an unknown page or the 3-minute timeout. It never types, autofills or works around a challenge, and the tab stays open for the person.

`agent-lb reauth <email> --auto` runs one enrolled account by hand. Manual reauth, recard and auto runs share one lock per profile.

## Backoff and pages
- One run at a time (`auto-reauth.lock`), accounts in turn. At most one try per account every 6 hours and 3 a day; after a stuck result, none for 24 hours unless the ask is answered.
- A stuck or `bind_not_live` result on a `reauth_required` account files one owner ask from Factory (`rails_ask.ask_owner`). It is never filed again while open and is withdrawn when the account turns active. Option 2 points at the account's dashboard page with `?signin=1`, which starts a fresh flow with the paste box (flows live 15 minutes, so the ask holds none).
- Success is a log line. `error` goes to Factory's thread at most once a day.
- `run --dry-run` clicks nothing, files nothing and keeps its own state file, so a second dry tick shows the dedupe. `run --now <id>` tries an enrolled account whatever its status.
- `account-health-watch` stops posting `reauth_required` for enrolled accounts and posts nothing for openai accounts or the refused prefixes.

## Keep-alive
Once a week per enrolled profile, at an hour hashed from its email, the same tick visits myaccount.google.com (Google-login profiles) and claude.ai in background tabs for up to 60 seconds with zero clicks, then closes the Chrome it opened. It skips a profile whose Chrome is already open. Its value is early detection plus a claude.ai cookie refresh; it does not extend Google sessions. A lapsed session joins the account's one open ask; a Cloudflare or unknown result is only logged.

## Files
`~/.agent-lb/state/auto-reauth.log` (one line per result), `auto-reauth-state.json` (tries, ask ids), `auto-reauth-state.dry.json`, `profile-locks/`.
