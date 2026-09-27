#!/usr/bin/env bash
# Question 1, pipeline steps: build the pipeline package (a stand-in for
# ihsDataR) once per commit in a no-network container, then run a pipeline
# from the read-only library in another. Output: evidence/pipeline_probe.txt
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${1:-datalab-agent:dev}"
W="$HERE/runs/pipeline-$(date +%s)-$$"
mkdir -p "$W/lib" "$W/in/oracle" "$W/out" "$W/result" "$W/step"
chmod 0777 "$W/lib" "$W/out" "$W/result"
FLAGS=(--rm --pull never --label datalab.spike=runner --network none --read-only
  --tmpfs /tmp:rw,size=512m,mode=1777,nosuid,nodev,noexec --user 10004:10004
  --cap-drop ALL --security-opt no-new-privileges --init --pids-limit 256 --memory 2g)
tree_sum() { (cd "$HERE" && find fakeDataR -type f | sort | xargs shasum | shasum | cut -c1-16); }
before=$(tree_sum)
echo "== build: R CMD INSTALL from a read-only source into a writable library (no network)"
s=$(date +%s.%N)
docker run "${FLAGS[@]}" \
  --mount "type=bind,source=$HERE/fakeDataR,target=/run/src/fakeDataR,readonly" \
  --mount "type=bind,source=$W/lib,target=/run/lib" \
  "$IMAGE" sh -c 'cp -r /run/src/fakeDataR /tmp/pkg && R CMD INSTALL --no-docs --no-test-load --library=/run/lib /tmp/pkg 2>&1 | tail -2'
echo "build took $(echo "$(date +%s.%N) - $s" | bc) s"
echo "== source tree checksum before $before, after $(tree_sum)"

"${PY:-$(git -C "$HERE" rev-parse --show-toplevel)/backend/.venv/bin/python}" "$HERE/make_fake_data.py" "$W/in/oracle/IHS_SYN.DAILY_WEARABLE.csv" --as-of 2025-04-30
cat > "$W/step/step.json" <<JSON
{"step": "metrics", "type": "pipeline", "seed": 1, "params": {"start_date": "2025-04-01", "end_date": "2025-05-01"},
 "inputs": {"IHS_SYN.DAILY_WEARABLE": {"path": "/run/in/oracle/IHS_SYN.DAILY_WEARABLE.csv"}},
 "outputs": {"weekly": "/run/out/weekly_steps.csv"}}
JSON
echo 'fakeDataR::run_pipeline("weekly_steps")' > "$W/step/script.R"
echo "== run the pipeline from the read-only library"
s=$(date +%s.%N)
docker run "${FLAGS[@]}" --env R_LIBS=/opt/ihs/lib \
  --mount "type=bind,source=$W/lib,target=/opt/ihs/lib,readonly" \
  --mount "type=bind,source=$HERE/datalab,target=/run/datalab,readonly" \
  --mount "type=bind,source=$W/step,target=/run/step,readonly" \
  --mount "type=bind,source=$W/in/oracle,target=/run/in/oracle,readonly" \
  --mount "type=bind,source=$W/out,target=/run/out" \
  --mount "type=bind,source=$W/result,target=/run/result" \
  "$IMAGE" Rscript --vanilla /run/datalab/run_step.R; echo "exit=$?"
echo "run took $(echo "$(date +%s.%N) - $s" | bc) s"
head -3 "$W/out/weekly_steps.csv"; cat "$W/result/result.json" | tr -d '\n' | head -c 300; echo
