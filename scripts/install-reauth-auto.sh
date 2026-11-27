#!/usr/bin/env bash
set -euo pipefail

if [[ $(uname -s) != Linux || $(hostname -s) != ax42 ]]; then
  printf '%s\n' 'Install reauth automation on the ax42 Linux account owner only.' >&2
  exit 1
fi

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../deploy/systemd" && pwd)"
units="$HOME/.config/systemd/user"
state="$HOME/.agent-lb-primary/state"
mkdir -p "$units"
install -d -m 0700 "$state/reauth-verified"
install -m 0644 "$source_dir"/agent-lb-reauth-{auto,keepalive,needs-you}.{service,timer} "$source_dir"/agent-lb-reauth-needs-you.path "$units"
systemctl --user daemon-reload
systemctl --user enable --now agent-lb-reauth-{auto,keepalive,needs-you}.timer agent-lb-reauth-needs-you.path
