#!/bin/zsh
# Publishes anonymized aggregate LLM usage to a public GitHub repo (durable backup + CDN-served).
# Pushes via a repo-scoped SSH deploy key. Idempotent, single-flight, fail-soft.
set -euo pipefail
LOG="$HOME/.codex-lb/llm-usage-publish.log"
LOCK="$HOME/.codex-lb/.llm-usage-publish.lock"
REPO_DIR="$HOME/.codex-lb/llm-usage-repo"
ENDPOINT="http://127.0.0.1:2455/api/usage/public"
REMOTE="git@github.com:aneym/llm-usage.git"
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/llm-usage-deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
mkdir "$LOCK" 2>/dev/null || { echo "$(date -u +%FT%TZ) skip: already running" >>"$LOG"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
if [ ! -d "$REPO_DIR/.git" ]; then git clone "$REMOTE" "$REPO_DIR" >>"$LOG" 2>&1; fi
cd "$REPO_DIR"
git pull --ff-only >>"$LOG" 2>&1 || true
ok=0
for d in 7 30 90 365; do
  if curl -fsS -m 30 "$ENDPOINT?days=$d" -o "usage-$d.json.tmp"; then mv "usage-$d.json.tmp" "usage-$d.json"; ok=1; else rm -f "usage-$d.json.tmp"; echo "$(date -u +%FT%TZ) warn: fetch days=$d failed" >>"$LOG"; fi
done
[ -f usage-365.json ] && cp usage-365.json usage.json
if [ "$ok" = "0" ]; then echo "$(date -u +%FT%TZ) error: all fetches failed; keeping last snapshot" >>"$LOG"; exit 0; fi
# Idempotency: revert any snapshot whose ONLY change vs HEAD is the generatedAt
# timestamp, so unchanged usage does not churn a commit on every cron tick.
for f in usage.json usage-7.json usage-30.json usage-90.json usage-365.json; do
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
if git diff --cached --quiet; then echo "$(date -u +%FT%TZ) no change" >>"$LOG"; else
  git -c user.name="llm-usage-bot" -c user.email="llm-usage-bot@users.noreply.github.com" commit -m "usage snapshot $(date -u +%FT%TZ)" >>"$LOG" 2>&1
  git push origin HEAD:main >>"$LOG" 2>&1 && echo "$(date -u +%FT%TZ) pushed" >>"$LOG"
fi
