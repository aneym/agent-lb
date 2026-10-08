#!/bin/bash
# Scheduled health refresh shares the hook's cache, proxy isolation and hysteresis.
exec python3 "$HOME/.jev/hooks/jev-health.py" refresh
