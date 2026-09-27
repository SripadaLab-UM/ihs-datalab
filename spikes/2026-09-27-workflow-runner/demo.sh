#!/usr/bin/env bash
# Questions 2 and 4, end to end: run, replay twice, replay with another seed,
# run again on later data, a QC failure that blocks delivery, and a step that
# misbehaves, then the controls. Output: evidence/demo.txt
# Start from an empty runs/ so section 8 lists only this demo's deliveries.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-$(git rev-parse --show-toplevel)/backend/.venv/bin/python}
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
S=$(latest)
echo; echo "### 5. run again on today's (later) data"
$PY runner.py again "$A" --source runs/sources/fake_2025-07-15.csv
echo; echo "### 6. small cells not suppressed: QC fails, nothing is delivered"
$PY runner.py run $WF --source runs/sources/fake_2025-05-31.csv --param suppress_small_cells=false || true
Q=$(latest)
$PY -c "import json; s=[s for s in json.load(open('$Q/record.json'))['steps'] if s['id']=='check_summary'][0]; print('  ', [c for c in s['result']['checks'] if c['id']=='small_cells'])"
echo; echo "### 7. a misbehaving step"
$PY runner.py run workflows/misbehaving.yaml --source runs/sources/fake_2025-05-31.csv || true
M=$(latest)
$PY -c "import json,sys; s=json.load(open('$M/record.json'))['steps'][1]; print({k: s[k] for k in ('status','exit_code','missing_outputs','undeclared_files_dropped')}); print(s['result']['messages'])"
echo "outputs folder: $(ls -A "$M/steps/sneaky/outputs" | wc -l | tr -d ' ') files"
echo; echo "### 8. delivered folders"
find runs/destinations -name datalab-export.json | sort

echo; echo "### 9. controls"
echo "replay with another seed (section 4): columns that differ:"
$PY - "$A" "$S" <<'PYEOF'
import csv, sys
a, d = (list(csv.DictReader(open(f"{r}/steps/summary/outputs/weekly_by_device.csv"))) for r in sys.argv[1:])
print(" ", sorted({k for ra, rd in zip(a, d) for k in ra if ra[k] != rd[k]}))
PYEOF
echo "two fresh runs, same source and seed (re-extraction with ORDER BY):"
$PY runner.py run $WF --source runs/sources/fake_2025-05-31.csv --seed 42 --no-deliver | head -1
F1=$(latest)
$PY runner.py run $WF --source runs/sources/fake_2025-05-31.csv --seed 42 --no-deliver | head -1
F2=$(latest)
$PY runner.py compare "$F1" "$F2" | $PY -c "import json, sys; print('  identical:', json.load(sys.stdin)['identical'])"
echo "an image that isn't on disk, with --pull never:"
docker run --rm --pull never --label datalab.spike=runner --network none \
  sha256:0000000000000000000000000000000000000000000000000000000000000000 true 2>&1 | head -1 | sed 's/^/  /' || true
cp runs/image-facts-*.json evidence/image_facts.json
$PY check_migration.py "$A" > evidence/check_migration.txt
