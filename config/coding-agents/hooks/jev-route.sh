#!/bin/bash
# Alert-only; all network refreshes run detached from the prompt path.
exec python3 "$HOME/.jev/hooks/jev-health.py"
