"""`datalab` command line: run the app and a few maintainer tasks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from datalab.config import load_settings


def main(argv: list[str] | None = None) -> int:
    from datalab import __version__

    parser = argparse.ArgumentParser(prog="datalab")
    parser.add_argument("--version", action="version", version=f"datalab {__version__}")
    parser.add_argument("--profile", choices=["real", "practice"], help="default: real")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run DataLab")
    serve.add_argument("--no-browser", action="store_true", help="don't open the browser")
    trial = commands.add_parser("try", help="ask one question in a new data session (for testing)")
    trial.add_argument("question")
    trial.add_argument("--image", default="datalab-agent:dev")
    trial.add_argument("--research", action="store_true", help="a research session instead")
    setup = commands.add_parser("setup", help="save the lab's settings and your keys")
    setup.add_argument("--settings", type=Path, help="the lab's DataLab settings file")
    setup.add_argument("--update", action="store_true", help="replace keys already saved")
    remove = commands.add_parser("uninstall", help="remove DataLab's containers, keys, and data")
    choice = remove.add_mutually_exclusive_group()
    choice.add_argument("--delete-data", action="store_true", default=None)
    choice.add_argument("--keep-data", dest="delete_data", action="store_false")
    commands.add_parser("pull-images", help="download the pinned container images")
    commands.add_parser("backup", help="back up DataLab's database now")
    back = commands.add_parser(
        "rollback", help="restore the database from a backup, for an older DataLab"
    )
    back.add_argument("--list", action="store_true", help="list the backups and stop")
    back.add_argument("--backup", metavar="NAME", help="the backup to restore (default: newest)")
    back.add_argument(
        "--yes", action="store_true", help="restore even though it drops what was recorded since"
    )
    commands.add_parser("db-check", help="connect and report what the session may do")
    safety = commands.add_parser("safety-check", help="run the Safety check and print the results")
    safety.add_argument(
        "--strict", action="store_true", help="fail if a required check couldn't be verified"
    )

    catalog = commands.add_parser("catalog", help="build the schema catalog (metadata only)")
    catalog.add_argument("--out", type=Path, required=True, help="catalog folder to write")
    source = catalog.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-export", type=Path, help="a CSV metadata export folder")
    source.add_argument("--from-database", action="store_true", help="read the configured database")

    kb_check = commands.add_parser(
        "kb-check", help="check a knowledge base folder (the check GitHub Actions runs)"
    )
    kb_check.add_argument("path", type=Path, help="the knowledge base's folder")
    kb_check.add_argument("--fix", action="store_true", help="rewrite index.md if it's stale")
    kb_check.add_argument("--format", choices=["text", "github", "json"], default="text")
    kb_check.add_argument(
        "--allow-data-hits",
        action="store_true",
        help="report possible participant data without failing (people confirmed them on save)",
    )

    args = parser.parse_args(argv)
    if args.command == "kb-check":
        # Needs no settings: it runs in GitHub Actions too.
        from datalab.knowledge.check import run as run_kb_check

        if not args.path.is_dir():
            print(f"{args.path} isn't a folder.", file=sys.stderr)
            return 2
        return run_kb_check(
            args.path, fix=args.fix, output=args.format, allow_data=args.allow_data_hits
        )
    settings = load_settings(args.profile)

    if args.command == "serve":
        return _serve(settings, open_browser=not args.no_browser)

    if args.command == "try":
        import asyncio

        from datalab.trial import run_trial

        return asyncio.run(run_trial(settings, args.question, args.image, args.research))

    if args.command == "setup":
        from datalab.setup import setup as run_setup

        return run_setup(args.profile, args.settings, update=args.update)

    if args.command == "uninstall":
        from datalab.setup import uninstall

        return uninstall(delete_data=args.delete_data)

    if args.command == "pull-images":
        return _pull_images(settings)

    if args.command == "backup":
        return _backup(settings)

    if args.command == "rollback":
        return _rollback(settings, args)

    if args.command == "db-check":
        return _db_check(settings)

    if args.command == "safety-check":
        import asyncio

        from datalab.trial import run_safety_check

        return asyncio.run(run_safety_check(settings, strict=args.strict))

    if args.command == "catalog":
        return _build_catalog(settings, args)
    return 2


def _serve(settings, *, open_browser: bool) -> int:
    from datalab.trial import refuse_if_running

    # The data folder's lock, held until DataLab exits: nothing else may
    # change the database while an interrupted update is sorted out, or after.
    refuse_if_running(settings)
    import threading
    import webbrowser

    import uvicorn

    from datalab import __version__, db, updates
    from datalab.app import create_app
    from datalab.db.backups import BackupFailed
    from datalab.web import BrowserSession

    # An update that didn't finish is sorted out before anything opens the database.
    recovery = updates.recover(
        settings.data_dir,
        settings.database_file,
        app_version=__version__,
        known=db.known_migrations(),
    )
    if recovery is not None:
        print(recovery.message, flush=True)
    browser = BrowserSession(settings.port)
    try:
        app = create_app(settings, browser=browser, web_dist=_web_dist())
    except db.DatabaseNewerThanApp as error:
        print(error)
        return 1
    except BackupFailed as error:
        print(
            f"DataLab couldn't back up its database before updating it ({error}), "
            "so it didn't change anything. If the disk is full, free up some space, then "
            "start it again."
        )
        return 1
    # The new version is up, with its migrations applied: the update is done.
    updates.finish(settings.data_dir, __version__)
    url = f"http://{settings.host}:{settings.port}{browser.sign_in_path()}"
    print(f"DataLab ({settings.profile}) is starting. Open: {url}", flush=True)
    if open_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    # Open event streams from browser tabs never end on their own, so give
    # shutdown a few seconds, then stop sessions and containers regardless.
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="warning",
        timeout_graceful_shutdown=5,
    )
    return 0


def _pull_images(settings) -> int:
    import subprocess

    from datalab.sessions.containers import GATEWAY_IMAGE, PROXY_IMAGE

    images = [GATEWAY_IMAGE, PROXY_IMAGE]
    if "/" in settings.agent_image:  # from a registry; a local development image isn't
        images.insert(0, settings.agent_image)
    else:
        print(f"Using the local agent image {settings.agent_image} (not downloaded).")
    for image in images:
        print(f"Downloading {image.split('@')[0]} …", flush=True)
        if subprocess.run(["docker", "pull", "-q", image]).returncode != 0:
            print(f"Couldn't download {image}. Is Docker Desktop running?")
            return 1
    return 0


def _backup(settings) -> int:
    import sqlite3

    from datalab import __version__
    from datalab.datalock import refuse_second_instance
    from datalab.db.backups import backups_dir, take_backup

    # A running DataLab may be backing up or removing old backups itself.
    refuse_second_instance(settings.data_dir, settings.profile)
    if not settings.database_file.exists():
        print("There's no DataLab database yet, so there's nothing to back up.")
        return 1
    source = sqlite3.connect(settings.database_file, isolation_level=None)
    try:
        backup = take_backup(
            source, backups_dir(settings.database_file), app_version=__version__, reason="manual"
        )
    finally:
        source.close()
    print(f"Backed up the database to {backup.folder}")
    return 0


def _rollback(settings, args) -> int:
    from datalab import __version__, db
    from datalab.db import rollback
    from datalab.db.backups import BackupFailed, backups_dir, list_backups

    known = db.known_migrations()
    if args.list:
        found = list_backups(backups_dir(settings.database_file))
        if not found:
            print(f"No backups in {backups_dir(settings.database_file)}")
        for backup in reversed(found):
            usable = "" if set(backup.migrations) <= known else "  (for a newer DataLab)"
            print(
                f"{backup.name:<30} {backup.created_at[:16].replace('T', ' ')}  "
                f"{backup.reason:<8} schema {backup.schema_version}{usable}"
            )
        return 0

    from datalab.trial import refuse_if_running

    # Held from before the plan until the restore is done (and this process
    # exits), so nothing is recorded in between that the restore would lose.
    refuse_if_running(settings)
    try:
        plan = rollback.plan(settings.database_file, known, choose=args.backup)
    except rollback.RollbackRefused as refused:
        print(refused)
        return 1
    backup = plan.backup
    print(
        f"This DataLab ({__version__}) is older than your database, which a newer DataLab "
        f"changed ({', '.join(plan.undone)})."
    )
    print(
        f"Rolling back puts back the backup {backup.name!r}, taken "
        f"{backup.created_at[:16].replace('T', ' ')} before DataLab {backup.app_version} "
        "changed the database."
    )
    if plan.carried:
        print("\nThe Data accessed log is kept: these are carried over into the restored database:")
        for line in rollback.describe(plan.carried):
            print(f"  {line}")
    if plan.loses_data:
        print("\nThat drops what was recorded in DataLab since then:")
        for line in rollback.describe(plan.losses):
            print(f"  {line}")
        print(
            "\nThe files themselves (conversation workspaces, query results, and exports) "
            "stay on disk; only DataLab's record of them goes."
        )
        if not args.yes:
            print("Nothing was changed. To roll back anyway, run: datalab rollback --yes")
            return 1
    try:
        kept = rollback.restore(settings.database_file, backup, app_version=__version__)
    except (rollback.RollbackRefused, BackupFailed) as refused:
        print(f"Nothing was changed: {refused}")
        return 1
    print(
        f"Rolled back. The database as it was a moment ago is kept in {kept.folder}, "
        "in case you need it."
    )
    return 0


def _web_dist() -> Path | None:
    """The built web UI: packaged with a release, or the repo's frontend/dist."""
    import os

    here = Path(__file__).resolve().parent
    for candidate in (
        os.environ.get("DATALAB_WEB_DIST"),
        here / "web_dist",
        here.parents[2] / "frontend" / "dist",
    ):
        if candidate and (Path(candidate) / "index.html").exists():
            return Path(candidate)
    return None


def _database(settings):
    from datalab.credentials import oracle_password
    from datalab.data.oracle import OracleDatabase

    if settings.oracle is None:
        sys.exit(f"No database configured in {settings.settings_file}")
    return OracleDatabase(settings.oracle, oracle_password(settings.oracle), settings.limits)


def _db_check(settings) -> int:
    privileges = _database(settings).session_privileges()
    print(f"Enabled roles:     {', '.join(sorted(privileges.enabled_roles)) or '(none)'}")
    print(f"System privileges: {', '.join(sorted(privileges.system_privileges)) or '(none)'}")
    for schema, privilege in sorted(privileges.non_read_object_privileges):
        print(f"Non-read grant:    {privilege} on {schema}")
    print("Read-only:", "yes" if privileges.is_read_only else "NO")
    return 0 if privileges.is_read_only else 1


def _build_catalog(settings, args) -> int:
    from datalab.data.catalog import Catalog

    if args.from_export:
        catalog = Catalog.from_metadata_export(args.from_export)
    else:
        database = _database(settings)
        connection = database.connect()
        try:
            catalog = Catalog.from_database(connection, sorted(settings.oracle.allowed_schemas))
        finally:
            connection.close()
    catalog.save(args.out)
    print(f"Wrote {len(catalog)} tables and views for {', '.join(catalog.schemas)} to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
