> **Throwaway spike (2026-09-27), for milestone 6.** This is evidence for
> the workflow runner design in [docs/WORKFLOWS.md](../../docs/WORKFLOWS.md)
> and [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) §7. It is not
> product code. All data is invented by `make_fake_data.py` (IDs are
> `SYN-####`); no database was queried.

# Workflow runner spike

Run on macOS with Docker Desktop 29.6.2 (linux/arm64), image
`datalab-agent:dev`, R 4.6.1, 178 installed R packages
([evidence/image_facts.json](evidence/image_facts.json)), and
the backend's Python 3.13 venv (for `pyyaml`, `sqlglot` and DataLab's own
`exports.py`). A parallel session rebuilt `datalab-agent:dev` between the
first runs and the regenerated evidence: `probe_sandbox.txt` and
`pipeline_probe.txt` used `sha256:f0f76b62c888…`, and `demo.txt` used
`sha256:03c31729eff3…`. The R package fingerprint was the same for both
(`ae05a133…`). The tag moved while the packages stayed put, which is why runs
pin the digest.

The scripts default to `backend/.venv/bin/python` in this checkout; set `PY`
to use another interpreter with `pyyaml` and `sqlglot`.

## Layout

| Path | What |
|---|---|
| `probe_sandbox.sh` | Q1: the step sandbox flags, what each one does, and startup cost |
| `race_probe.sh` | Q1: a Docker Desktop bind-mount problem found on the way |
| `mount_flag_probe.sh` | Q1: `-v` and `--mount` when the host folder doesn't exist |
| `pipeline_probe.sh`, `fakeDataR/` | Q1: build a pipeline package (a stand-in for `ihsDataR`) with no network, run a pipeline from it |
| `datalab/run_step.R` | Q2: DataLab's step wrapper: reads the contract, sets the seed, writes `result.json` |
| `runner.py` | Q2, Q4, Q6: the prototype driver: `run`, `again`, `replay`, `compare` |
| `workflows/*.yaml` | the prototype workflow, and one whose R step misbehaves |
| `check_reads.py` | Q3: `reads:` against the SQL steps |
| `0008_workflow_runs.sql`, `check_migration.py` | Q6: draft migration, loaded with a real run record (checked against 0001–0005 only) |
| `demo.sh` | Q2 and Q4 end to end, then the controls; run it with an empty `runs/` |
| `evidence/` | output of every script above |
| `cleanup.sh` | removes containers labelled `datalab.spike=runner`, and nothing else |

Run folders go to `runs/` (ignored by git).

## 1. R steps with no network

**Yes.** The agent image runs R, pipeline and custom QC steps with this flag
set ([evidence/probe_sandbox.txt](evidence/probe_sandbox.txt)):

```
docker run --rm --pull never --name <run>-<step> --label datalab.run=<run>
  --network none
  --read-only --tmpfs /tmp:rw,size=1g,mode=1777,nosuid,nodev,noexec
  --user 10004:10004 --cap-drop ALL --security-opt no-new-privileges --init
  --pids-limit 256 --memory 4g --memory-swap 4g --cpus 2
  --env OMP_NUM_THREADS=1 --env OPENBLAS_NUM_THREADS=1 --env R_DATATABLE_NUM_THREADS=1 --env TZ=Etc/UTC
  --mount type=bind,source=<DataLab>/run_step.R dir,target=/run/datalab,readonly
  --mount type=bind,source=<step>/spec,target=/run/step,readonly
  --mount type=bind,source=<upstream>/outputs,target=/run/in/<name>,readonly   (one per input)
  --mount type=bind,source=<step>/scratch,target=/run/out
  --mount type=bind,source=<step>/result,target=/run/result
  --workdir /run/out
  <image by digest> Rscript --vanilla /run/datalab/run_step.R
```

What each part was shown to do:

| Flag | Evidence |
|---|---|
| `--network none` | only `lo` is up (the other names are the kernel's stub tunnel devices, all down), 0 routes, no DNS, R's `url()` fails |
| `--read-only` | `/etc`, `/home/agent` and the inputs refuse writes; `/run/out` accepts them |
| `--tmpfs /tmp` | **needed**: without it R can't start (`Fatal error: creating temporary file for '-e' failed`) |
| `noexec` on `/tmp` | a script written there can't run. Pure-R packages still load; anything that compiles at run time (`Rcpp::sourceCpp`) can't, which is what we want |
| `--user 10004:10004` | the image already says `USER agent`; stating it means a changed image can't run steps as root |
| `--cap-drop ALL`, `no-new-privileges` | `CapEff 0`, `CapBnd 0`, `NoNewPrivs 1` |
| `--memory` (with `--memory-swap` equal) | a 3 GB allocation in a 2 GB container exits 137 |
| `--pids-limit` | starting 300 background processes against a limit of 256 hits the limit (`Cannot fork`); the exact count at which it stops wasn't captured |
| `--init` | **needed**: `docker stop` takes 0.1 s with it, 10.2 s without, because Rscript as PID 1 ignores SIGTERM. Cancel would hang otherwise |
| `--pull never` | an image that isn't there fails at once (`No such image`) instead of reaching a registry |
| `--mount` over `-v` | for a host folder that doesn't exist, `-v` succeeds and creates it; `--mount` fails and creates nothing ([evidence/mount_flag_probe.txt](evidence/mount_flag_probe.txt)) |
| labels | in the app, `datalab.run`, `datalab.profile` and `datalab.instance`, so startup cleanup removes only this instance's leftover step containers, as `remove_all_session_containers` does for sessions (the spike used `datalab.spike=runner`) |

**Pipeline steps** ([evidence/pipeline_probe.txt](evidence/pipeline_probe.txt)):
the package is built once per commit with `R CMD INSTALL` in the same
no-network sandbox (source mounted read-only and copied to `/tmp`, library
folder writable; 1.1 s for a pure-R package), then mounted read-only at
`/opt/ihs/lib` with `R_LIBS` for pipeline runs. The source tree's checksum is
unchanged by the build. `ihsDataR` has no compiled code today; if it gains
some, build it the same way (the `.so` lands in the library folder, not the
noexec `/tmp`).

**Compared with session containers** (`sessions/containers.py`): the same
`--init`, `--cap-drop ALL`, `no-new-privileges`, and CPU and memory limits.
Different: steps have `--network none` instead of the internal network and
`--dns`; a read-only root with a noexec `/tmp`; an explicit `--user`; a
lower pids limit (256, not 1024); `--rm`; `--pull never`; the image by
digest; and `--mount` rather than `-v`. Steps need no gateway, no token, no
env file and no Codex home.

**Startup overhead** (5 runs each, seconds): `docker run … sh -c true` 0.27;
`Rscript -e 1` 0.5; `Rscript` loading the tidyverse 1.1; `docker exec` into
an already-running container 0.27. A fresh container per step costs about
0.3 s over a warm one. In the prototype, whole container steps took
0.28–0.82 s ([evidence/demo.txt](evidence/demo.txt)). Not
worth a warm pool: a fresh container per step also gives each step a clean
`/tmp` and exactly its own mounts.

**Found on the way: Docker Desktop can't mount a folder deleted and recreated
at the same path** ([evidence/race_probe.txt](evidence/race_probe.txt)). The
first probe re-run failed every mount with `bind source path does not
exist`. Reproduced: 9 of 10 mounts failed when the folder was deleted and
recreated right after a container had used it; 0 of 10 with a new path each
time. The stale folder became mountable again after a few minutes. So
**every run and every step gets a new folder, and nothing is deleted and
recreated in place**. Replay makes a new run folder and copies the kept
inputs in.

## 2. The input and output contract

Proposed, and working in `runner.py` plus `datalab/run_step.R`: **files on
disk, one JSON spec, and a result file with the exit code**.

- **`/run/step/step.json`** (read-only, written by DataLab) is the manifest:
  step id and type, `seed`, `params` (typed), `inputs` (for each: its path
  under `/run/in/<name>/`, sha256, bytes, rows, columns), and `outputs`
  (name to path under `/run/out/`). One JSON file, not environment
  variables: parameters keep their types, it can be checksummed and kept, and
  it is the same for R, pipeline and QC steps.
- **`/run/in/<name>/`** is each declared input: the upstream step's
  *collected* outputs folder, read-only. Only declared inputs are mounted.
- **`/run/datalab/run_step.R`** (DataLab's own code) loads the spec into
  `params`, `inputs$<name>`, `outputs$<name>` (as in WORKFLOWS.md's examples),
  sets the RNG in full, sources `/run/step/script.R`, and writes
  **`/run/result/result.json`**: `{status, step, counts, messages, checks,
  r_version}`. Helpers: `datalab_count()`, `datalab_message()`,
  `datalab_check(id, passed, observed, expected, message)`.
- **Exit codes:** 0 ok; 1 the script raised an error (its message goes in
  `result.json`); 3 a custom check failed. The host treats a step as passed
  only if the exit code is 0, `result.json` says `ok` and every declared
  output is present.
- **Only declared outputs come back.** After the container exits, DataLab
  `lstat`s each declared output, takes only regular files (opened with
  `O_NOFOLLOW`), copies them into a new `outputs/` folder with checksums,
  counts and drops everything else, and removes the scratch folder.
  `result.json` is capped at 64 KB and the log at 1 MB.

**QC.** Built-in checks run in DataLab's host code on the collected CSV and
return the same `checks` list as custom R checks, each `{id, status:
pass|fail, observed, expected, message}`: `min_rows`, `required_columns`,
`no_missing`, `max_missing: {COL: share}`, `unique_by` (duplicate count) and
`small_cells: {count_column, min}` (rows whose count is between 1 and
min − 1). Values like `min: $min_cell` come from the parameters.
**Messages hold counts, shares and column names only, never values or
keys**, since they go into the run record, the UI and the delivery manifest.
A failed check stops the run; later steps are `skipped` and nothing is
delivered.

Demonstrated in [evidence/demo.txt](evidence/demo.txt), on the chain SQL
extract (2,440 fake rows) → built-in QC → R weekly summary with bootstrap CIs
and small-cell suppression → built-in QC including the small-cell rule →
custom R check → delivery through the real `exports.export()`:

- §1: every step passes and one file is delivered with a manifest.
- §6: the same run with suppression off fails `small_cells` (9 cells below
  11), skips the custom check and delivers nothing.
- §7: an R step that makes its declared output a link to `/etc/passwd`,
  writes an undeclared file, tries the network and writes to its input: the
  link isn't collected (`missing_outputs`), the extra file is dropped, the
  network is blocked, the input write is refused, and the step fails.

## 3. Declared inputs

Proposed format: a top-level **`reads:`** list in the workflow file, and the
same list in each pipeline's `inst/pipelines/<name>/pipeline.yaml`
(example: `fakeDataR/inst/pipelines/weekly_steps/pipeline.yaml`):

```yaml
reads:
  - IHS_2025.VFITBITDAILYDATA            # short form
  - object: IHS_2025.VGARMINDAILY        # long form, for pipelines
    columns: [STUDY_PARTICIPANT_ID, RECORD_DATE, STEPS]
    where: RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD') AND RECORD_DATE < TO_DATE(:end_date, 'YYYY-MM-DD')
```

Checks (`check_reads.py`, [evidence/check_reads.txt](evidence/check_reads.txt)),
using sqlglot's Oracle parser, as the SQL check does:

- every object a SQL step names must be `SCHEMA.OBJECT` and in `reads:`
  (joins and subqueries are caught; CTE names don't count; unqualified names
  and database links are refused);
- a workflow that runs a pipeline must list the pipeline's `reads:`;
- every `reads:` entry must be used, so the list can't drift.

How DataLab enforces it:

1. **At save time**, in the workflow-file check (DataLab's own code), with
   the participant-data scan.
2. **At run time**, SQL steps pass the declared set to the data service,
   which already gets the tables from `check_sql`; a table outside the set is
   refused before the query runs, and logged as rejected. The prototype's
   `FakeDataService` does this; `DataService.run_query` would take an
   `allowed_tables` argument.
3. **Pipelines can't read anything else**: they run with no network and no
   data-service route, so the declared objects, which DataLab extracts
   through `run_query` (a generated `SELECT <columns> FROM <object> WHERE
   <where>`, with the workflow's parameters as binds) into
   `/run/in/oracle/<SCHEMA>.<OBJECT>.csv`, are their only data. The
   declaration is the mechanism. A `where:` (or an explicit
   `whole_table: true`) should be required, so a pipeline can't pull a
   whole cohort table by accident; the usual row and byte caps still apply.

## 4. Replay determinism

Verified byte for byte ([evidence/demo.txt](evidence/demo.txt), §2–§5 and
the controls in §9):

- two Replays of a run reproduce every output, and every QC result, exactly;
- **control**: a Replay with a different seed changes only the two
  bootstrap CI columns, so the seed is what makes it reproducible;
- two fresh Runs over the same data with the same seed are identical too,
  because the SQL has `ORDER BY`;
- Run again over later data gives new results, as it should: 4,240
  extracted rows, and 48 summary rows (16 weeks × 3 devices).

What a Replay must pin, and how:

| Thing | How |
|---|---|
| The definition | the workflow text kept in the record, plus the repo commit; hash the **git blob**, not the working-tree bytes (see §5, line endings) |
| Pipeline code | the `ihs-pipelines` commit and the pipeline's tree id; the built library, cached per commit, with its checksum |
| The environment | the image by its **platform** digest, run with `--pull never`. A fingerprint of `installed.packages()` is recorded as a check. No renv: packages are baked into the image from a dated Posit snapshot and nothing is installed at run time (no network, noexec `/tmp`), so the digest pins them |
| The inputs | the kept extracts, whose sha256 is checked before a Replay starts |
| Randomness | one run seed, recorded; each step's seed is derived from it and the step id, so adding a step doesn't change the others'; the wrapper sets `RNGkind` in full, so a change of R's defaults can't change results |
| The runtime | `TZ=Etc/UTC`, `LC_ALL=C.UTF-8` (from the image), one thread for BLAS, OpenMP and data.table |
| DataLab's side | the wrapper's checksum and DataLab's version |

What still breaks exactness: code that writes the time, temp names or
unordered results into its outputs; parallel reductions; and **a different
CPU architecture**. The release builds the agent image separately for
`linux/amd64` (Windows) and `linux/arm64` (Macs), so a Replay is promised
byte-identical only on the same platform. Across platforms, compare with a
numeric tolerance and say so. Not tested here.

## 5. Windows (from the docs; not tested)

- **Paths.** DataLab calls `docker` with an argument list, not a shell, so
  plain `C:\Users\…` paths work in `--mount source=…`; the `//c/…` form is
  only needed from Git Bash or MSYS, whose path rewriting mangles them
  ([docker/for-win#2620](https://github.com/docker/for-win/issues/2620),
  [Medium](https://medium.com/@kale.miller96/how-to-mount-your-current-working-directory-to-your-docker-container-in-windows-74e47fa104d7)).
  `--mount` is comma-separated, so refuse or quote a data-folder path with a
  comma. Paths with spaces are fine.
- **Speed.** DataLab's data folder is on NTFS, which the WSL2 backend shares
  through a 9P file share; bind mounts from there are much slower than from
  the WSL filesystem ([Docker: WSL 2 best practices](https://www.docker.com/blog/docker-desktop-wsl-2-best-practices/),
  [File sharing with Docker Desktop](https://www.docker.com/blog/file-sharing-with-docker-desktop/)).
  Fine for these CSVs; measure with a large extract on the Windows machine.
- **`--network none`** is a Linux network namespace inside Docker Desktop's
  VM, the same on WSL2 as on a Mac. Nothing Windows-specific is expected; the
  Safety check should still run the interface and DNS checks there.
- **Ownership.** Files on the Windows side have no Linux owner; WSL maps
  permissions, and `chown` in a container does nothing on the host
  ([cr0x.net](https://cr0x.net/en/docker-bind-mount-permissions-windows/)).
  `readonly` is enforced by the mount itself, so it holds. On native Linux
  Docker (CI), files a step writes are owned by uid 10004; the host can read
  them but may not delete them, so run steps there as the host's uid, or
  hand the folders over first.
- **File locking.** Windows won't delete or rename a file another process
  has open: antivirus scanning new files, Dropbox syncing, Excel with a
  delivered CSV open ([Microsoft](https://techcommunity.microsoft.com/t5/windows-blog-archive/the-case-of-the-mysterious-locked-file/ba-p/723349),
  [Docker forum](https://forums.docker.com/t/files-getting-locked-on-shared-volume-using-docker-on-windows/139744)).
  The design avoids renaming or overwriting: run folders are written once,
  Replay writes a new one, and delivery creates new files (`export()` opens
  with `x`). Removing a step's scratch folder should retry and then give up
  quietly; `os.replace` onto an open file fails on Windows, so don't.
- **Line endings.** Git on Windows may check out the workflow file and R
  scripts with CRLF, which changes their checksums. Record git blob ids, and
  set `* text=auto eol=lf` in `ihs-pipelines`' `.gitattributes`. The data
  service writes CSVs with `\r\n` (Python's `csv` default) on every platform,
  so extracts are the same bytes everywhere.
- **Names.** NTFS ignores case and refuses some names, so step ids and output
  file names should be lower case, unique ignoring case, and pass
  `exports.effective_name`/`safe_name`. Keep run paths well under 260
  characters.

## 6. Run records

The run folder keeps the files: `workflow.yaml`, each step's `spec/`,
`outputs/`, `result/result.json` and `log.txt`, and `record.json` (a full
copy of the record, so a folder explains itself). SQLite holds the rest
(draft [`0008_workflow_runs.sql`](0008_workflow_runs.sql)). The draft was
checked only against migrations 0001–0005, which are what this branch has.
0006, which adds `queries.origin` (`conversation`, `playground` or `run`)
and is coming in wave 0, wasn't included. The draft doesn't touch
`queries`, and its run ids (`run_…`) are the ids 0006 expects for
`origin = 'run'`, but it should be rechecked once 0006 lands:

- **`workflow_runs`**: id (`run_…`, as 0006's `queries.origin = 'run'`
  expects), mode (`run`, `run_again`, `replay`) and `of_run`, `set_id`,
  status and times, who started it; `repo_commit`, workflow path, blob id and
  text; pipelines with their tree ids and library checksums; image ref,
  platform digest and platform, R package fingerprint, runner version;
  params, seed, `reads`; the run folder; whether the inputs are still kept;
  and, for a Replay, whether it reproduced.
- **`workflow_run_steps`**: kind and status, seed; for SQL steps the query
  id, SQL and binds (copied from `queries`, which Storage cleanup may
  remove); exit code and time; inputs (step and sha256) and outputs (file,
  sha256, bytes, rows, columns); and `result.json` or the built-in checks.
- **`workflow_run_deliveries`**: the destination key and id, the path it was
  then, the dated folder `export()` made, files and manifest checksum.

What each action reads: **Run again** takes the definition, params and
seed, and re-extracts. **Replay** takes the definition text, params, seed,
image digest and pipeline libraries, and the kept inputs (checked by
checksum); it doesn't deliver unless the person asks. **Delivery** calls
`exports.export(destination / folder, title=<workflow name>, tag=<run id>,
sources=<outputs opened with O_NOFOLLOW>, about={workflow, run, qc})`,
which gave a dated folder and a manifest in the prototype.

Two things to change for delivery ([evidence/check_migration.txt](evidence/check_migration.txt)):

- A workflow file names its destination (`destination: dropbox-ihs-2025`),
  but `export_destinations.name` is a label that needn't be unique, and ids
  are random per computer. The draft adds a unique `key` column that the
  person sets in Settings.
- **`DestinationStore.add` does `INSERT INTO export_destinations VALUES (?,
  ?, ?, ?)`**, which fails once the table gains a column ("5 columns but 4
  values"). Name the columns there before (or with) 0008.

## Recommended runner design

- One asyncio task per run in the host process, steps in order. Each run and
  step gets a new folder; the record is written as each step finishes, so a
  crash leaves an `interrupted` run, not a lost one.
- SQL steps go through `DataService.run_query` with `session_id = run id`,
  origin `run`, and the declared `reads:` as the allowed tables. Extracts
  are kept in the run folder for Replay.
- R, pipeline and custom QC steps each get a fresh container with the flag
  set in §1, the image by platform digest, and the contract in §2. A host
  deadline kills the container by name on timeout or Stop.
- Built-in QC is host code over the collected CSVs. Every check reports
  counts only. Any failure stops the run before delivery.
- Pipelines are built per commit in the same sandbox and mounted read-only.
- Delivery goes through `exports.export()` to the destination key's folder,
  and is recorded in `workflow_run_deliveries` and the audit log.

## Risks

- Replay is exact only on the same CPU architecture (§4).
- Docker Desktop's stale mounts of recreated folders (§1). The new-folder
  rule avoids them, but any code that deletes and recreates a mounted path
  will fail intermittently.
- A step can fill the disk through `/run/out`. Check free space before each
  step and cap output size afterwards, or give `/run/out` a size limit.
- The `/tmp` tmpfs counts against the container's memory limit.
- Custom checks and R steps can still put values in `result.json` messages.
  They're reviewed code, but DataLab should cap and show messages as data.
- Pipelines' `reads:` extraction can be large. Require `where:`, and keep
  the row and byte caps.
- Linux CI and native Docker hosts: output ownership (§5).
- Windows is untested: mounts, speed, locking and CRLF all need checking on
  the Windows machine in milestone 7.
- Replay needs the old image still on disk. Keep release images, and when
  one is missing, offer to pull that exact digest.
