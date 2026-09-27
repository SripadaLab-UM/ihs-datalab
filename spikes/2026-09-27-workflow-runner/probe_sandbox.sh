#!/usr/bin/env bash
# Question 1: can an R step run in the agent image with no network, a
# read-only root, read-only inputs and one writable output folder? Which
# flags are needed, and what does a step container cost to start?
#
# Every container is labelled datalab.spike=runner and started with --rm;
# cleanup.sh removes anything left by label.
#
#   ./probe_sandbox.sh [image]        (default: datalab-agent:dev)
set -uo pipefail
IMAGE="${1:-datalab-agent:dev}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# A new folder every time: Docker Desktop often cannot mount a folder that was
# deleted and recreated at the same path (see race_probe.sh).
SCRATCH="$HERE/runs/probe-$(date +%s)-$$"
mkdir -p "$SCRATCH/in/extract" "$SCRATCH/out"
printf 'id,value\n1,2\n3,4\n' > "$SCRATCH/in/extract/data.csv"
chmod 0777 "$SCRATCH/out"   # uid 10004 must be able to write here on Linux

# The proposed flag set for a workflow step container.
STEP_FLAGS=(
  --rm --pull never --label datalab.spike=runner
  --network none
  --read-only
  --tmpfs /tmp:rw,size=512m,mode=1777,nosuid,nodev,noexec
  --user 10004:10004
  --cap-drop ALL
  --security-opt no-new-privileges
  --init
  --pids-limit 256 --memory 2g --memory-swap 2g --cpus 2
  --mount "type=bind,source=$SCRATCH/in,target=/run/in,readonly"
  --mount "type=bind,source=$SCRATCH/out,target=/run/out"
  --workdir /run/out
)

section() { printf '\n== %s\n' "$*"; }
run() { docker run "${STEP_FLAGS[@]}" "$IMAGE" "$@"; }

section "image"
docker image inspect "$IMAGE" --format 'id={{.Id}} user={{.Config.User}}'

section "network: interfaces and an outside lookup"
run sh -c 'ls /sys/class/net; getent hosts example.org && echo RESOLVED || echo "no DNS (expected)"'
run Rscript -e 'r <- try(readLines(url("http://1.1.1.1"), n = 1), silent = TRUE); cat(if (inherits(r, "try-error")) "R HTTP failed (expected)\n" else "R HTTP WORKED\n")'

section "filesystem: root read-only, inputs read-only, outputs writable"
run sh -c 'touch /etc/x 2>&1 | tail -1; touch /home/agent/x 2>&1 | tail -1; touch /run/in/extract/x 2>&1 | tail -1; touch /run/out/ok && echo "wrote /run/out/ok"'
ls -ln "$SCRATCH/out"

section "identity and privileges"
run sh -c 'id; grep -E "^(CapEff|CapBnd|NoNewPrivs)" /proc/self/status'

section "R works: read the input, write an output, packages load"
run Rscript -e 'suppressMessages({library(dplyr); library(readr); library(jsonlite)}); x <- read.csv("/run/in/extract/data.csv"); write.csv(x, "/run/out/copy.csv", row.names = FALSE); cat("R", as.character(getRversion()), "tempdir", tempdir(), "\n")'

section "without --tmpfs /tmp, R cannot start (read-only root)"
docker run --rm --pull never --label datalab.spike=runner --network none --read-only \
  --user 10004:10004 --cap-drop ALL "$IMAGE" Rscript -e 'cat("started\n")' 2>&1 | head -3
echo "exit=$?"

section "noexec /tmp: a compiled package install would fail, which is what we want"
run sh -c 'printf "#!/bin/sh\necho ran\n" > /tmp/x.sh; chmod +x /tmp/x.sh; /tmp/x.sh 2>&1 | tail -1'

section "memory limit: allocating 3 GB in a 2 GB container"
run Rscript -e 'x <- numeric(3e9/8); x[] <- 1; cat("allocated\n")' >/dev/null 2>&1
echo "exit=$? (137 = killed by the OOM killer)"

section "pids limit: 300 background sleeps against --pids-limit 256"
run sh -c 'for i in $(seq 1 300); do sleep 30 2>/dev/null & done; echo "processes: $(ls -d /proc/[0-9]* | wc -l)"' 2>&1 | sort | uniq -c | tail -3

section "network: only lo, no routes"
run sh -c 'for d in /sys/class/net/*/; do printf "%s=%s " $(basename $d) $(cat $d/operstate); done; echo; echo "routes: $(tail -n +2 /proc/net/route | wc -l)"'

section "stopping: with --init, docker stop is immediate"
name=spike-runner-stop-init
docker run -d --name $name "${STEP_FLAGS[@]}" "$IMAGE" Rscript -e 'Sys.sleep(600)' >/dev/null
sleep 1; s=$(date +%s.%N); docker stop -t 10 $name >/dev/null; e=$(date +%s.%N)
echo "with --init: $(echo "$e - $s" | bc) s"
name=spike-runner-stop-noinit
NOINIT=(); for f in "${STEP_FLAGS[@]}"; do [ "$f" != "--init" ] && NOINIT+=("$f"); done
docker run -d --name $name "${NOINIT[@]}" "$IMAGE" Rscript -e 'Sys.sleep(600)' >/dev/null
sleep 1; s=$(date +%s.%N); docker stop -t 10 $name >/dev/null; e=$(date +%s.%N)
echo "without --init: $(echo "$e - $s" | bc) s (Rscript as PID 1 ignores SIGTERM)"

section "startup overhead (5 runs each, wall seconds)"
t() { local s e; s=$(date +%s.%N); "$@" >/dev/null 2>&1; e=$(date +%s.%N); printf '%.2f ' "$(echo "$e - $s" | bc)"; }
printf 'docker run, sh -c true:          '; for i in 1 2 3 4 5; do t run sh -c true; done; echo
printf 'docker run, Rscript -e 1:        '; for i in 1 2 3 4 5; do t run Rscript -e 1; done; echo
printf 'docker run, Rscript + tidyverse: '; for i in 1 2 3 4 5; do t run Rscript -e 'suppressMessages(library(tidyverse))'; done; echo
printf 'host Rscript -e 1 (if installed): '; if command -v Rscript >/dev/null; then for i in 1 2 3; do t Rscript -e 1; done; echo; else echo "none"; fi
WARM=(); for f in "${STEP_FLAGS[@]}"; do [ "$f" != "--rm" ] && WARM+=("$f"); done
docker run -d --name spike-runner-warm "${WARM[@]}" "$IMAGE" sleep 600 >/dev/null
printf 'docker exec into a warm one, Rscript -e 1: '; for i in 1 2 3 4 5; do t docker exec spike-runner-warm Rscript -e 1; done; echo
docker rm -f spike-runner-warm >/dev/null
