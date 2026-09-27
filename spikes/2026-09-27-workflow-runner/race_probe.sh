#!/usr/bin/env bash
# Docker Desktop (macOS): during the first probe run, folders that were
# deleted and recreated at the same path right after a container that had
# mounted them exited became unmountable ("bind source path does not exist")
# and stayed that way for minutes. How often, and do fresh paths avoid it?
HERE="$(cd "$(dirname "$0")" && pwd)"
mnt() { docker run --rm --label datalab.spike=runner --mount "type=bind,source=$1,target=/x,readonly" alpine:3 true >/dev/null 2>&1 && echo ok || echo FAIL; }
N=${1:-10}
fail=0
for i in $(seq 1 "$N"); do
  p="$HERE/runs/reuse-$$"; rm -rf "$p"; mkdir -p "$p/in"
  [ "$(mnt "$p/in")" = FAIL ] && fail=$((fail+1))
done
echo "same path, deleted and recreated each time: $fail of $N mounts failed"
fail=0
for i in $(seq 1 "$N"); do
  p="$HERE/runs/fresh-$$-$i"; mkdir -p "$p/in"
  [ "$(mnt "$p/in")" = FAIL ] && fail=$((fail+1))
done
echo "a new path each time: $fail of $N mounts failed"
