#!/usr/bin/env bash
# Start / stop / reset the synthetic IHS Oracle Free container.
#
# DEV ONLY. The container holds only fake, generated data, so the passwords
# below are fixed and public on purpose. Never reuse them anywhere real.
#
#   synthetic/db.sh start      start (or create) the container and wait until ready
#   synthetic/db.sh stop       stop the container (data is kept)
#   synthetic/db.sh reset      delete the container and start a fresh one
#   synthetic/db.sh status     print container and database health
#   synthetic/db.sh generate   start, then (re)load the synthetic data (drop & recreate)
#   synthetic/db.sh verify     check privileges, row counts and quirks as DATALAB_RO
set -euo pipefail

NAME=datalab-synthetic-oracle
IMAGE=container-registry.oracle.com/database/free:latest-lite
PORT="${SYNTH_ORACLE_PORT:-1522}"
# Password for SYS / SYSTEM / PDBADMIN inside the container (dev-only).
export SYNTH_ORACLE_PWD="${SYNTH_ORACLE_PWD:-SynthDev2026}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

inspect() { local s; s="$(docker inspect -f "$1" "$NAME" 2>/dev/null | tr -d '\n')"; echo "${s:-missing}"; }
state() { inspect '{{.State.Status}}'; }
health() { inspect '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'; }

wait_ready() {
  local deadline=$((SECONDS + ${SYNTH_ORACLE_WAIT:-600}))
  printf 'Waiting for Oracle in %s to be ready' "$NAME"
  # The image's HEALTHCHECK reports "healthy" once FREEPDB1 is open.
  until [[ "$(health)" == healthy ]]; do
    if [[ "$(state)" != running ]]; then echo; echo "container is $(state)"; docker logs --tail 40 "$NAME"; exit 1; fi
    if (( SECONDS > deadline )); then echo; echo "timed out"; docker logs --tail 40 "$NAME"; exit 1; fi
    printf '.'; sleep 3
  done
  echo " ready: localhost:${PORT}/FREEPDB1"
}

start() {
  case "$(state)" in
    running) ;;
    missing)
      # Published on 127.0.0.1 only: the dev passwords are public, so the
      # database must not be reachable from other computers.
      docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:1521" \
        -e ORACLE_PWD="$SYNTH_ORACLE_PWD" "$IMAGE" >/dev/null
      echo "created $NAME (first start takes a minute or two)" ;;
    *) docker start "$NAME" >/dev/null ;;
  esac
  wait_ready
}

case "${1:-}" in
  start) start ;;
  stop) docker stop "$NAME" >/dev/null && echo stopped ;;
  reset) docker rm -f "$NAME" >/dev/null 2>&1 || true; start ;;
  status) echo "container: $(state), health: $(health), port: ${PORT}" ;;
  generate)
    start
    SYNTH_ORACLE_DSN="localhost:${PORT}/FREEPDB1" \
      uv run --project "$HERE" python "$HERE/generate.py" "${@:2}" ;;
  verify)
    SYNTH_ORACLE_DSN="localhost:${PORT}/FREEPDB1" uv run --project "$HERE" python "$HERE/verify.py" ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
