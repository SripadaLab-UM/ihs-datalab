#!/usr/bin/env bash
# Remove only this spike's containers, found by label.
ids=$(docker ps -aq --filter label=datalab.spike=runner)
[ -n "$ids" ] && docker rm -f $ids
echo "left: $(docker ps -aq --filter label=datalab.spike=runner | wc -l | tr -d ' ')"
