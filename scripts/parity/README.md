# Release parity check

A one-off check before v1 ships (docs/WORKFLOWS.md, "Release check against
the prototype"): the prototype's 8 default routines and `daily_metrics_2025`
run in both the prototype and v1 on the same synthetic inputs, and their
outputs are compared. The five per-metric workflows v1 adds (`steps_day`,
`daily_rhr_2025`, `active_minutes_2025`, `sleep_2025`, `daily_hrv_2025`) are
compared too, each with the file of the same name from the prototype's
`daily_metrics_2025` run. The latest results are in
`docs/acceptance/2026-09-27-parity.md`.

**Synthetic data only.** v1 runs in the practice profile, which refuses any
database without the synthetic marker. The prototype is pointed at the same
local database (`datalab-synthetic-oracle`, 127.0.0.1:1522). The password
is read from where v1 keeps it (the keychain, or `DATALAB_ORACLE_PASSWORD`)
and passed to each side in its environment. It is never printed.

## Running it

Needs Docker, the synthetic database running, the images
`datalab-agent:dev` (v1) and `lab-ai-routine-r:local` (the prototype's R
sandbox), and a catalog folder for v1.

```sh
cd backend
uv run python ../scripts/parity/parity.py all \
  --work <scratch folder> \
  --prototype <prototype checkout> \
  --pipelines <ihs-pipelines checkout> --commit <sha> \
  --catalog <catalog folder> \
  --report ../docs/acceptance/<date>-parity.md
```

`all` runs v1, then the prototype twice, then compares. `v1`, `prototype`
and `compare` run one part. `--only` limits it to some workflows.

- **Only the harness's settings reach each side.** `DATALAB_*` and
  `LAB_AI_*` variables in the shell are dropped before either side starts.
- **The prototype is only read.** A `--no-local` clone of it goes in
  `<work>/proto`, with its own virtualenv. `proto_driver.py` runs with that
  clone's Python and imports its code.
- **v1** is a private practice DataLab on port 8802, with a new data folder
  under `<work>` each time (Docker Desktop can't always mount a folder that
  was deleted and made again at the same path). Its `[workflows] folder` is
  a `git archive` of the pipelines repo at `--commit`, so the files can't
  change during the run. The harness signs in with the one-time link and
  drives `/api/workflows`, then copies each run's files from its run
  folder.
- **The prototype's broker** runs on 127.0.0.1:8812, only for the live
  pass, inside `proto_driver.py`.
- **Clean-up** touches only what the harness started: the v1 server's
  process group (by PID), containers labelled with that data folder's
  `datalab.instance`, and `routine_r_…` containers that weren't there
  before the prototype ran (they are `--rm`, so normally there are none).

## Two passes

1. **End to end** (`live`). Each side extracts from the synthetic database
   itself: the prototype through its own Oracle broker and live executor,
   v1 through its data service. This is the prototype as it ran, and it
   catches differences in extraction, such as how a date is written.
2. **Same extract** (`extracts`). The prototype's broker is replaced by a
   stand-in that hands back v1's extract, byte for byte, so both sides' R
   code runs on identical input. This isolates the R steps, QC and delivery
   paths.

Delivery in the prototype uses its own `DropboxDeliveryService` with a
client that copies into the output folder instead of uploading, so its own
path rules decide where each file goes. v1 delivers to its practice folder.

## What's compared

For each workflow: the extract (for `daily_metrics_2025`, one per Oracle
object), each output file, the QC outcome (passed, and the row count
checked), and each delivered file, by subfolder and name.

Verdicts, strictest first (compare.py):

| Verdict | Meaning |
|---|---|
| `identical` | the same bytes |
| `same_rows` | the same rows in order; only CSV quoting or line endings differ |
| `same_rows_any_order` | the same rows in another order; allowed only where nothing fixes the order (extracts, whose SQL has no ORDER BY, and the routines' R steps, which keep it) |
| `equal_within_tolerance` | numbers agree to a relative 1e-9, nothing else differs |
| `differs` | anything else, with the difference per column |
| `v1_only` | a check only v1 has (the pipeline's workflow QC steps) |

**Tolerances, and why.** Numbers: relative 1e-9 (absolute 1e-12). R writes
15 significant digits, and two R versions, or a sum taken in another order,
can differ in the last one. Missing values spelt `NA` or empty count as the
same. Row order: only as above. No tolerance is applied to dates: a column
whose values differ only in how a date or time is written (`2025-05-03`,
`2025-05-03 00:00:00`, `2025-05-03T00:00:00`) still `differs`, and is
marked `datetime_format_only` so it can be told apart from a different
value. Where rows are in another order, they are lined up on the columns
that agree as a whole (dates compared as instants), and the report says
whether that key was unique.

**No row-level values.** The report holds column names, row and column
counts, value *shapes* (such as `YYYY-MM-DD HH:MM:SS`), and missing and
distinct counts. Never a row, a cell, a minimum or a maximum, nor a mean or
sum (for a column of one value, that is the value), nor error text, which
can quote a value: a failed step shows only its id and state. The run
folders and status files under `<work>` hold the rest, including the
synthetic rows, and stay there.

## What it doesn't cover

- **Dropbox itself.** The prototype's upload is replaced by a copy; v1
  writes to a folder. Only which files go where, and their bytes, are
  compared.
- **Other parameter values.** Each workflow runs once, with its defaults.
- **Real data.** Synthetic data has the real shapes and quirks, but not
  every quirk; cases the synthetic database doesn't hold (a Garmin HRV
  extract whose calendar dates are all at midnight, say) aren't exercised.
- **The prototype's other paths**: the agent's own broker queries, the
  capped (non-full) export, the UI, and Run again or Replay.
- **R versions.** Each side uses its own image (R 4.2.2 in the prototype's,
  R 4.6.1 in v1's). That is what ships, so differences it causes are
  reported, not hidden.

## Tests

```sh
cd backend && uv run pytest -q ../scripts/parity
```
