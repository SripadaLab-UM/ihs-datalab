"""Upgrade a data folder from the previous release, then roll it back.

    cd backend && uv run python ../scripts/upgrade-rollback-test.py [--from-ref <git ref>]

1. The previous release's own code (its `datalab.db`, taken from git) makes a
   database, and some synthetic conversations and queries go in it.
2. This commit's code opens it: the database must be backed up to
   `backups/<version>/` first, then migrated, with every row kept.
3. A conversation and a query are recorded, then the previous release rolls
   back with `datalab rollback`. Releases before milestone 7 don't have the
   command, and rolling back to them isn't supported, so the previous release
   is played by this commit's code with only its migrations (as the next
   release will roll back to this one). The rollback must refuse while
   DataLab is running and without --yes, then put back exactly the database
   from step 1, plus the new query: the Data accessed log is never rolled back.
4. The previous release's real code must open the rolled-back database, and
   this commit's code must upgrade it again.

The previous release is the newest v* tag before this commit, or a pinned
commit if there isn't one or it has the same migrations as this commit. The
test also checks, against every v* tag, that no released migration was
changed or removed.
Synthetic data only; everything happens in a temporary folder.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
MIGRATIONS = "backend/src/datalab/db/migrations"
# The first installable pre-release (v0.1.0-alpha.1): queries and conversations only.
PINNED = "b32dec60d2084d97e7ea4815d05eba4fc639d6de"
# Not a port anything else uses; the rollback command checks it's free.
PORT = 8785


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-ref", help="the release to upgrade from (default: see above)")
    args = parser.parse_args()
    ref = args.from_ref or previous_release()
    with tempfile.TemporaryDirectory(prefix="datalab-upgrade-") as scratch:
        run(ref, Path(scratch))
    print("Upgrade and rollback: all checks passed.")
    return 0


def previous_release() -> str:
    found = git("describe", "--tags", "--abbrev=0", "--match", "v*", "HEAD^", check=False)
    ref = found.strip() or PINNED
    if migration_names(ref) == migration_names("HEAD"):
        print(f"{ref} has the same migrations as this commit; upgrading from {PINNED} instead.")
        ref = PINNED
    return ref


def run(ref: str, scratch: Path) -> None:
    print(f"Upgrading from {ref} ({git('rev-parse', '--short', ref + '^{commit}').strip()})")
    old_names = migration_names(ref)
    new_names = migration_names("HEAD")
    for released in released_refs(ref):
        check_migrations_only_go_forward(released, new_names)
    if old_names == new_names:
        sys.exit(f"{ref} has the same migrations as this commit, so there's nothing to test.")

    old_src = scratch / "old"
    extract(ref, ["backend/src/datalab/__init__.py", "backend/src/datalab/db"], old_src)
    old_src = old_src / "backend" / "src"
    data = scratch / "data"
    data.mkdir()
    (data / "settings.toml").write_text(f"port = {PORT}\n", encoding="utf-8")
    database = data / "datalab.sqlite"

    step("The previous release makes a database with synthetic content")
    python(old_src, f"from datalab import db; db.connect(Path({str(database)!r})).close()")
    fill(database)
    before = snapshot(database)
    expect(schema(database) == old_names, "the database has the previous release's migrations")

    step("This commit's code upgrades it")
    python(None, f"from datalab import db; db.connect(Path({str(database)!r})).close()")
    expect(schema(database) == new_names, "all of this commit's migrations were applied")
    [backup] = manifests(data)
    expect(backup["reason"] == "migrate", "a backup was taken before migrating")
    expect(backup["migrations"] == old_names, "the backup holds the database from before")
    expect(snapshot(data / "backups" / backup["folder"] / "datalab.sqlite") == before, "…exactly")
    expect(snapshot(database, like=before) == before, "every row survived the upgrade")
    if "0011_export_folders.sql" in new_names and "0011_export_folders.sql" not in old_names:
        check_new_columns(database)

    step("A conversation and a query are recorded, then the previous release rolls back")
    connection = sqlite3.connect(database, isolation_level=None)
    connection.execute(
        "INSERT INTO conversations (id, kind, mode, title, model, created_at, updated_at) "
        "VALUES ('c-new', 'data', 'explore', 'Recorded after the update', 'gpt-5.5', "
        "'2026-09-27T12:00:00', '2026-09-27T12:00:00')"
    )
    connection.execute(
        "INSERT INTO queries (id, session_id, started_at, status, sql_text) VALUES "
        "('q-new', 'c-new', '2026-09-27T12:01:00', 'succeeded', 'SELECT 1 FROM dual')"
    )
    connection.close()
    # What the rollback must keep: every query, in the previous release's columns.
    expected = {**before, "queries": snapshot(database, like=before)["queries"]}
    previous = previous_release_with_rollback(ref, old_names, scratch / "previous")
    from datalab.datalock import hold

    with hold(data):  # a DataLab is running on this data folder
        busy = datalab(previous, data, "rollback", "--yes")
    expect(
        busy.returncode != 0 and "already using the data folder" in busy.stdout + busy.stderr,
        "while DataLab holds the data folder's lock, rollback refuses",
    )
    expect(schema(database) == new_names, "…and changes nothing")
    refused = datalab(previous, data, "rollback")
    expect(
        refused.returncode == 1 and "Nothing was changed" in refused.stdout,
        "without --yes, rollback refuses",
    )
    expect("Recorded after the update" in refused.stdout, "…and says what it would drop")
    expect(schema(database) == new_names, "…and changes nothing")
    done = datalab(previous, data, "rollback", "--yes")
    expect(
        done.returncode == 0 and "Rolled back" in done.stdout,
        "with --yes, rollback restores the backup",
    )
    expect(schema(database) == old_names, "the database has the previous release's layout again")
    expect(snapshot(database) == expected, "…the rows it had before the update, and every query")
    kept = [m for m in manifests(data) if m["reason"] == "restore"]
    expect(len(kept) == 1 and kept[0]["migrations"] == new_names, "what it replaced was kept")

    step("The previous release opens it, and this commit upgrades it again")
    python(old_src, f"from datalab import db; db.connect(Path({str(database)!r})).close()")
    expect(snapshot(database) == expected, "the previous release opens it unchanged")
    python(None, f"from datalab import db; db.connect(Path({str(database)!r})).close()")
    expect(schema(database) == new_names, "upgrading again works")
    expect(snapshot(database, like=before) == expected, "…with every row kept")


def released_refs(ref: str) -> list[str]:
    """Every v* tag with migrations, and the release being upgraded from."""
    tags = git("tag", "--list", "v*").split()
    with_migrations = [t for t in tags if git("ls-tree", f"{t}:{MIGRATIONS}", check=False)]
    return sorted({*with_migrations, ref})


def check_migrations_only_go_forward(ref: str, new: list[str]) -> None:
    old = migration_names(ref)
    missing = [m for m in old if m not in new]
    expect(not missing, f"{ref}: no released migration was removed or renamed ({missing})")
    changed = [
        m
        for m in old
        if git("show", f"{ref}:{MIGRATIONS}/{m}") != git("show", f"HEAD:{MIGRATIONS}/{m}")
    ]
    expect(not changed, f"{ref}: no released migration was changed ({changed})")


def fill(database: Path) -> None:
    """Synthetic conversations, messages, queries, export folders and a delivered
    workflow run, in whichever of those tables the database has. Columns are
    named: later migrations add columns to these tables."""
    connection = sqlite3.connect(database, isolation_level=None)
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    for n in range(1, 4):
        when = f"2026-09-0{n}T10:00:00"
        if "conversations" in tables:
            connection.execute(
                "INSERT INTO conversations (id, kind, mode, title, model, created_at, updated_at) "
                "VALUES (?, 'data', 'explore', ?, 'gpt-5.5', ?, ?)",
                (f"c{n}", f"Synthetic question {n}", when, when),
            )
            connection.execute(
                "INSERT INTO events (conversation_id, seq, created_at, type, data_json) "
                "VALUES (?, 1, ?, 'user_message', ?)",
                (f"c{n}", when, json.dumps({"text": f"How many in cohort {n}?"})),
            )
        connection.execute(
            "INSERT INTO queries (id, session_id, started_at, finished_at, status, sql_text, "
            "row_count) VALUES (?, ?, ?, ?, 'succeeded', 'SELECT COUNT(*) FROM IHS_2025.T', 1)",
            (f"q{n}", f"c{n}", when, when),
        )
        if "export_destinations" in tables:
            connection.execute(
                "INSERT INTO export_destinations (id, name, path, added_at) VALUES (?, ?, ?, ?)",
                (f"dest_{n}", f"Synthetic folder {n}", f"/synthetic/exports/{n}", when),
            )
    if "workflow_run_deliveries" in tables:
        when = "2026-09-04T10:00:00"
        connection.execute(
            "INSERT INTO workflow_runs (id, workflow_name, mode, status, started_at, finished_at, "
            "started_by, workflow_path, workflow_source, workflow_blob, workflow_text, image_ref, "
            "image_digest, image_platform, host_platform, r_packages_sha256, runner_version, "
            "runtime_json, params_json, seed, reads_json, run_dir, delivery_status, "
            "delivery_message) VALUES ('run_20260904T100000_aaaaaa', 'synthetic_extract', 'run', "
            "'succeeded', ?, ?, 'Synthetic Person <s@example.org>', 'workflows/synthetic.yaml', "
            "'file', 'sha256:00', 'name: synthetic_extract', 'datalab-r:dev', 'sha256:11', "
            "'linux/arm64', 'linux/arm64', 'sha256:22', '0.0.0', '{}', '{}', 1, '[]', "
            "'runs/run_20260904T100000_aaaaaa', 'delivered', '1 file delivered.')",
            (when, when),
        )
        connection.execute(
            "INSERT INTO workflow_run_deliveries (id, run_id, destination_key, destination_id, "
            "destination_path, folder, files_json, manifest_sha256, delivered_at) VALUES "
            "('dl_1', 'run_20260904T100000_aaaaaa', 'synthetic-dropbox', 'dest_1', "
            "'/synthetic/exports/1', '/synthetic/exports/1/2026-09-04 1000 synthetic', "
            "'[]', 'sha256:33', ?)",
            (when,),
        )
    connection.close()


def check_new_columns(database: Path) -> None:
    """What 0011 added: folders offered by default, and no name or sync app
    recorded for deliveries made before it."""
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        offered = [r[0] for r in connection.execute("SELECT offered FROM export_destinations")]
        expect(offered and set(offered) == {1}, "export folders are offered after 0011")
        delivered = connection.execute(
            "SELECT destination_name, sync_provider FROM workflow_run_deliveries"
        ).fetchall()
        expect(
            delivered and all(tuple(r) == (None, None) for r in delivered),
            "…and earlier deliveries have no folder name or sync app recorded",
        )
    finally:
        connection.close()


def snapshot(database: Path, like: dict | None = None) -> dict:
    """Every row of every table, or of the tables and columns in `like`."""
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        if like is None:
            tables = {
                r[0]: [c[1] for c in connection.execute(f'PRAGMA table_info("{r[0]}")')]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT IN ('schema_migrations') AND name NOT LIKE 'sqlite_%'"
                )
            }
        else:
            tables = {t: list(v["columns"]) for t, v in like.items()}
        return {
            table: {
                "columns": columns,
                "rows": sorted(
                    list(r)
                    for r in connection.execute(
                        f'SELECT {", ".join(chr(34) + c + chr(34) for c in columns)} FROM "{table}"'
                    )
                ),
            }
            for table, columns in sorted(tables.items())
        }
    finally:
        connection.close()


def schema(database: Path) -> list[str]:
    connection = sqlite3.connect(database)
    try:
        return sorted(r[0] for r in connection.execute("SELECT name FROM schema_migrations"))
    finally:
        connection.close()


def manifests(data: Path) -> list[dict]:
    found = []
    for manifest in sorted((data / "backups").glob("*/manifest.json")):
        found.append(
            {**json.loads(manifest.read_text(encoding="utf-8")), "folder": manifest.parent.name}
        )
    return found


def previous_release_with_rollback(ref: str, old_names: list[str], folder: Path) -> Path:
    """This commit's code, with only the previous release's migrations and version."""
    shutil.copytree(SRC / "datalab", folder / "datalab", ignore=shutil.ignore_patterns("web_dist"))
    migrations = folder / "datalab" / "db" / "migrations"
    for file in migrations.glob("*.sql"):
        if file.name not in old_names:
            file.unlink()
    old_init = git("show", f"{ref}:backend/src/datalab/__init__.py")
    found = re.search(r'^__version__ = "(.*)"', old_init, re.MULTILINE)
    version = found.group(1) if found else "0.0.0"
    (folder / "datalab" / "__init__.py").write_text(
        f'"""IHS DataLab."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    return folder


def datalab(src: Path, data: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(src), "DATALAB_PROFILE": "practice"}
    env["DATALAB_DATA_DIR"] = str(data)
    result = subprocess.run(
        [sys.executable, "-m", "datalab.cli", *args],
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    print(indent(result.stdout + result.stderr))
    return result


def python(src: Path | None, code: str) -> None:
    """Run code with `src`'s datalab package, or this commit's if None."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    script = f"from pathlib import Path\n{code}"
    if src is not None:
        env["PYTHONPATH"] = str(src)
        # Make sure it really is that code, not the installed package.
        check = (
            f"import datalab; assert datalab.__file__.startswith({str(src)!r}), datalab.__file__"
        )
        script = f"{check}\n{script}"
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        sys.exit(f"This failed:\n{code}\n{result.stdout}{result.stderr}")


def extract(ref: str, paths: list[str], into: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref, *paths], cwd=ROOT, capture_output=True, check=True
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(into, filter="data")


def migration_names(ref: str) -> list[str]:
    listing = git("ls-tree", "--name-only", f"{ref}:{MIGRATIONS}")
    return sorted(n for n in listing.split() if n.endswith(".sql"))


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace"
    )
    if check and result.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def step(text: str) -> None:
    print(f"\n{text}", flush=True)


def expect(condition: bool, what: str) -> None:
    if not condition:
        sys.exit(f"  FAILED: {what}")
    print(f"  ok: {what}", flush=True)


def indent(text: str) -> str:
    return "\n".join(f"    | {line}" for line in text.strip().splitlines())


if __name__ == "__main__":
    raise SystemExit(main())
