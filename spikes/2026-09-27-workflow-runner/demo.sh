#!/usr/bin/env bash
# Questions 2 and 4, end to end: run, replay twice, replay with another seed,
# run again on later data, a QC failure that blocks delivery, and a step that
# misbehaves. Output: evidence/demo.txt
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-/Users/ataxali/work/ihs_datalab/backend/.venv/bin/python}
WF=workflows/wearable_weekly_summary.yaml
mkdir -p runs/sources
$PY make_fake_data.py runs/sources/fake_2025-05-31.csv --as-of 2025-05-31
$PY make_fake_data.py runs/sources/fake_2025-07-15.csv --as-of 2025-07-15
latest() { ls -d runs/run_* | tail -1; }
echo "### 1. run (source as of 2025-05-31, end_date 2025-08-01)"
$PY runner.py run $WF --source runs/sources/fake_2025-05-31.csv --param end_date=2025-08-01
A=$(latest)
echo; echo "### 2. replay of $A"
$PY runner.py replay "$A"
echo; echo "### 3. replay again"
$PY runner.py replay "$A"
echo; echo "### 4. replay with a different seed (control: the seed matters)"
$PY runner.py replay "$A" --seed 1
echo; echo "### 5. run again on today's (later) data"
$PY runner.py again "$A" --source runs/sources/fake_2025-07-15.csv
echo; echo "### 6. small cells not suppressed: QC fails, nothing is delivered"
$PY runner.py run $WF --source runs/sources/fake_2025-05-31.csv --param suppress_small_cells=false || true
echo; echo "### 7. a misbehaving step"
$PY runner.py run workflows/misbehaving.yaml --source runs/sources/fake_2025-05-31.csv || true
M=$(latest)
$PY -c "import json,sys; s=json.load(open('$M/record.json'))['steps'][1]; print({k: s[k] for k in ('status','exit_code','missing_outputs','undeclared_files_dropped')}); print(s['result']['messages'])"
echo "outputs folder: $(ls -A "$M/steps/sneaky/outputs" | wc -l | tr -d ' ') files"
echo; echo "### 8. delivered folders"
find runs/destinations -name datalab-export.json | sort
