#!/usr/bin/env bash
# Question 1: -v and --mount when the host folder doesn't exist.
# Output: evidence/mount_flag_probe.txt
HERE="$(cd "$(dirname "$0")" && pwd)"
base="$HERE/runs/mountflag-$(date +%s)-$$"
mkdir -p "$base"
docker run --rm --pull never --label datalab.spike=runner -v "$base/by-v:/x" alpine:3 true 2>&1 | head -1
echo "-v exit=${PIPESTATUS[0]}; host folder created: $([ -d "$base/by-v" ] && echo yes || echo no)"
docker run --rm --pull never --label datalab.spike=runner --mount "type=bind,source=$base/by-mount,target=/x" alpine:3 true 2>&1 | head -1 | sed "s|$HERE|<spike>|"
echo "--mount exit=${PIPESTATUS[0]}; host folder created: $([ -d "$base/by-mount" ] && echo yes || echo no)"
