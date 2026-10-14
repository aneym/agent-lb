#!/bin/sh
set -eu

if [ "${AGENT_LB_DATABASE_MIGRATE_ON_STARTUP:-true}" = "true" ]; then
  python -m app.db.migrate upgrade
fi



export AGENT_LB_DATABASE_MIGRATE_ON_STARTUP=false

exec python -m app.cli --host 0.0.0.0 --port 2455
