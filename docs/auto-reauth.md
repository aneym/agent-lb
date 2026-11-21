# Auto-reauth

When an enrolled Anthropic account goes `reauth_required`, `agent-lb reauth-auto run` (launchd `com.aneyman.agent-lb-reauth-auto` on Studio, every 10 minutes) signs it in again in the account's own Chrome profile, reusing the Google and claude.ai session that profile already has. A person hears about it only when the flow needs something only they can give.

## Who it acts on
- Only accounts enrolled by account id: `agent-lb reauth-auto enroll <id or prefix>`, `unenroll`, `list`. The list is `~/.agent-lb/state/auto-reauth-enrolled.json`.
- `agent-lb reauth-auto drop <id or prefix> --why "<reason>"` takes an account the owner does not need out of renewal: the next tick withdraws its open ask, and `account-health-watch` stays quiet about its `reauth_required`. `enroll` brings it back.
- Never an account with `ownerInstance` set, never an openai account, and never the three account-id prefixes refused in `app/reauth_enroll.py`. Those log `skipped`.
- An enrolled account with no browser profile on the host logs `no_profile` and pages nobody.

## One run
1. `POST /api/oauth/start` with `expectedAccountId`. A server that binds flows echoes it back. The server then saves tokens only for that identity, on the browser callback and on the pasted code alike, and only into that row.
2. If the echo is missing (a server older than the bind), the run is `bind_not_live`: it reads the pages and checks the identity but never clicks Authorize and never sends a callback.
3. The flow opens in the profile's Chrome. Every second each frame is read. Three clicks are allowed, each a real mouse click after a fresh check for stuck markers in every frame: the Google account tile for this exact email, "Continue with Google" on the claude.ai login page (only for profiles added with `--login google`), and Claude's Authorize button after `/api/account` on the consent page shows this exact email.
4. After Authorize, Claude's code page (`platform.claude.com/oauth/code/callback`) shows the code on the page rather than in its URL. The run reads `code#state` from that tab, checks the state is this flow's, and sends it to `/api/oauth/manual-callback` with the flow id, so the server still saves tokens only for the bound account. The code is never logged or printed. A completed renewal logs `active (status active; sent the code Claude showed on its page; bound; clicks consent)`. Manual `agent-lb reauth` reads the code the same way.
5. Anything else stops the run as `stuck:<kind>`: password, code, passkey, captcha, "verify it's you", emailed sign-in code, signed out, wrong account, an unknown page or the 3-minute timeout. It never types, autofills or works around a challenge, and the tab stays open for the person.

`agent-lb reauth <email> --auto` runs one enrolled account by hand. Manual reauth, recard and auto runs share one lock per profile.

## Backoff and pages
- One run at a time (`auto-reauth.lock`), accounts in turn. At most one try per account every 6 hours and 3 a day; after a stuck result, none for 24 hours unless the ask is answered.
- A stuck or `bind_not_live` result on a `reauth_required` account files one owner ask from Factory (`rails_ask.ask_owner`, floor `account`). When `AGENT_LB_SIGNIN_LANE` is set the ask goes into that Rails lane; on Studio `scripts/install-reauth-auto.sh` writes it into the launchd job (`AGENT_LB_SIGNIN_LANE=3e630a1028c8`, the "Claude sign-ins on agent-lb" lane). Unset, the ask files with no lane. Each tick reads the ask from Rails. While it is open nothing new is filed, and Rails' own duplicate refusal hands back the open one. It is withdrawn when the account turns active.
- One ask per account per day at most. Once the owner answers or clears it, that cause raises no new ask until the account changes: a renewal that works (or a keep-alive that comes back clean, for `lapsed:` causes) resets it, and a different stuck cause may ask again after a day. Picking an option retries at once; clearing or "not needed" keeps the 24-hour backoff. Option 2 points at the account's dashboard page with `?signin=1`, which starts a fresh flow with the paste box (flows live 15 minutes, so the ask holds none).
- Success is a log line. `error` goes to Factory's thread at most once a day.
- `run --dry-run` clicks nothing, files nothing and keeps its own state file, so a second dry tick shows the dedupe. `run --now <id>` tries an enrolled account whatever its status.
- `account-health-watch` stops posting `reauth_required` for enrolled accounts and posts nothing for openai accounts or the refused prefixes.

## Sign-in mail
agent-lb never asks Claude or Google to send a sign-in mail. Its only clicks are the three in step 3 (`app/reauth_page.py` `click`: Google account tile, "Continue with Google", Authorize). It never types an email address; an email-login profile at the login page, or any page that says a code or link was sent, stops the run as `stuck:email_login` (`classify` and `stuck_in`). Manual `agent-lb reauth` only opens the window; the person decides what to click.

## GUI session
A profile Chrome started outside the Mac's GUI session (plain ssh) cannot reach the login keychain, so it drops the profile's stored cookies and signs the profile out. Every profile launch (`app/core/profile_launch.py`: reauth, auto runs, keep-alive, recard, `profiles open`) checks `launchctl managername`. In the GUI session (`Aqua`, which includes the launchd jobs) it starts Chrome directly; anywhere else it starts Chrome as the launchd job `com.agent-lb.chrome.<cdp port>` in `gui/<uid>`, with the plist under `~/.agent-lb/state/chrome-jobs/`. If that domain refuses (nobody signed in to the Mac), the launch fails with an error that says so and starts nothing.

## Keep-alive
Once a week per enrolled profile, at an hour hashed from its email, the same tick visits myaccount.google.com (Google-login profiles) and claude.ai in background tabs for up to 60 seconds with zero clicks, then closes the Chrome it opened, even when a tab times out while closing. It skips a profile whose Chrome is already open. Its value is early detection plus a claude.ai cookie refresh; it does not extend Google sessions. A lapsed session joins the account's one open ask; a Cloudflare or unknown result is only logged.

## Files
`~/.agent-lb/state/auto-reauth.log` (one line per result), `auto-reauth-state.json` (tries, ask ids), `auto-reauth-state.dry.json`, `profile-locks/`.
