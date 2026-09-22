#!/bin/zsh
# Publishes anonymized aggregate LLM usage to a public GitHub repo (durable backup + CDN-served).
# Pushes via a repo-scoped SSH deploy key. Idempotent, single-flight, fail-soft.
# Source of truth: <agent-lb repo>/scripts/llm-usage-publish.sh
# Deployed copy: ~/.agent-lb/bin/llm-usage-publish.sh — the path named in
# ~/Library/LaunchAgents/com.aneyman.llm-usage-publish.plist (StartInterval 900).
set -euo pipefail
LOG="$HOME/.agent-lb/llm-usage-publish.log"
LOCK="$HOME/.agent-lb/.llm-usage-publish.lock"
FAIL_MARKER="$HOME/.agent-lb/llm-usage-publish.FAILING"
REPO_DIR="$HOME/.agent-lb/llm-usage-repo"
ENDPOINT="http://127.0.0.1:2455/api/usage/public"
REMOTE="git@github.com:aneym/llm-usage.git"
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/llm-usage-deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

# Single-flight with stale-lock reaping. A publish takes seconds; a lock dir
# older than 60 min is a leftover from a crash/reboot, not a live runner
# (exactly this wedge froze publishing 2026-07-12 -> 2026-08-24).
if ! mkdir "$LOCK" 2>/dev/null; then
  now=$(date +%s)
  lock_mtime=$(stat -f %m "$LOCK" 2>/dev/null || echo "$now")
  if [ $((now - lock_mtime)) -gt 3600 ]; then
    echo "$(date -u +%FT%TZ) warn: reaping stale lock (age $((now - lock_mtime))s)" >>"$LOG"
    rmdir "$LOCK" 2>/dev/null || rm -rf "$LOCK"
    mkdir "$LOCK" 2>/dev/null || { echo "$(date -u +%FT%TZ) skip: lock contended after reap" >>"$LOG"; exit 0; }
  else
    echo "$(date -u +%FT%TZ) skip: already running" >>"$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
if [ ! -d "$REPO_DIR/.git" ]; then git clone "$REMOTE" "$REPO_DIR" >>"$LOG" 2>&1; fi
cd "$REPO_DIR"
git pull --ff-only >>"$LOG" 2>&1 || true
ok=0
for d in 7 30 90 365 730; do
  if curl -fsS -m 30 "$ENDPOINT?days=$d" -o "usage-$d.json.tmp"; then mv "usage-$d.json.tmp" "usage-$d.json"; ok=1; else rm -f "usage-$d.json.tmp"; echo "$(date -u +%FT%TZ) warn: fetch days=$d failed" >>"$LOG"; fi
done
[ -f usage-365.json ] && cp usage-365.json usage.json
if [ "$ok" = "0" ]; then echo "$(date -u +%FT%TZ) error: all fetches failed; keeping last snapshot" >>"$LOG"; exit 0; fi
# Idempotency: revert any snapshot whose ONLY change vs HEAD is the generatedAt
# timestamp, so unchanged usage does not churn a commit on every cron tick.
for f in usage.json usage-7.json usage-30.json usage-90.json usage-365.json usage-730.json; do
  [ -f "$f" ] || continue
  git cat-file -e "HEAD:$f" 2>/dev/null || continue
  git show "HEAD:$f" > ".head.$f" 2>/dev/null || continue
  if python3 -c 'import json,sys
a=json.load(open(sys.argv[1])); b=json.load(open(sys.argv[2]))
a.pop("generatedAt",None); b.pop("generatedAt",None)
sys.exit(0 if a==b else 1)' "$f" ".head.$f"; then
    git checkout -- "$f" 2>/dev/null || cp ".head.$f" "$f"
  fi
  rm -f ".head.$f"
done
git add -A
if git diff --cached --quiet; then
  echo "$(date -u +%FT%TZ) no change" >>"$LOG"
else
  git -c user.name="llm-usage-bot" -c user.email="llm-usage-bot@users.noreply.github.com" commit -m "usage snapshot $(date -u +%FT%TZ)" >>"$LOG" 2>&1
fi
# Push whatever is unpushed (fresh commit or accumulated backlog). On failure,
# log AND drop a marker file a health check can watch; commits keep accumulating
# locally (fail-soft, snapshot history is never lost).
ahead=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
if [ "$ahead" -gt 0 ]; then
  if git push origin HEAD:main >>"$LOG" 2>&1; then
    echo "$(date -u +%FT%TZ) pushed ($ahead commit(s))" >>"$LOG"
    rm -f "$FAIL_MARKER"
  else
    echo "$(date -u +%FT%TZ) error: push failed; $ahead commit(s) accumulating locally" >>"$LOG"
    echo "$(date -u +%FT%TZ) push failed; $ahead commit(s) unpushed" >>"$FAIL_MARKER"
  fi
fi
