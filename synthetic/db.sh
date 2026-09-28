#!/usr/bin/env bash
# Start / stop / reset the synthetic IHS Oracle Free container (development and CI).
#
# DEV ONLY. The container holds only fake, generated data, so its passwords
# are fixed and public on purpose. Never reuse them anywhere real.
#
#   synthetic/db.sh start      start (or create) the container and wait until ready
#   synthetic/db.sh stop       stop the container (data is kept)
#   synthetic/db.sh reset      delete the container and its volume, and start a fresh one
#   synthetic/db.sh status     print the container's state
#   synthetic/db.sh generate   start, then (re)load the synthetic data (drop & recreate)
#   synthetic/db.sh verify     check privileges, row counts and quirks as DATALAB_RO
#
# The work is done by the same Python code practice DataLab runs itself
# (backend/src/datalab/practice_db), here for the development container
# datalab-synthetic-oracle on 127.0.0.1:${SYNTH_ORACLE_PORT:-1522}.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DATALAB_PRACTICE_DB_CONTAINER="${DATALAB_PRACTICE_DB_CONTAINER:-datalab-synthetic-oracle}"
export DATALAB_PRACTICE_DB_PORT="${SYNTH_ORACLE_PORT:-${DATALAB_PRACTICE_DB_PORT:-1522}}"

case "${1:-}" in
  start | stop | reset | status | generate | verify | setup)
    exec uv run --locked --project "$HERE/../backend" python -m datalab.practice_db "$@" ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
