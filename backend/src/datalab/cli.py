"""`datalab` command line: run the app and a few maintainer tasks."""

from __future__ import annotations

import argparse
import asyncio
import io
import re
import sys
from pathlib import Path
from typing import Any

from datalab.config import load_settings
from datalab.docker_path import ensure_docker_on_path


def main(argv: list[str] | None = None) -> int:
    from datalab import __version__

    _console_never_fails()
    # Docker Desktop's `docker`, when its link isn't on PATH (docker_path.py).
    ensure_docker_on_path()

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
    practice_db = commands.add_parser(
        "practice-db", help="practice DataLab's synthetic database (--profile practice only)"
    )
    practice_db_commands = practice_db.add_subparsers(dest="practice_db_command", required=True)
    practice_db_commands.add_parser("status", help="how the practice database stands")
    practice_db_commands.add_parser(
        "setup", help="start it, and load the made-up data if it has none (the installer runs this)"
    )
    practice_db_commands.add_parser("start", help="start it (the same as setup)")
    practice_db_commands.add_parser("stop", help="stop it; its data is kept")
    reset = practice_db_commands.add_parser(
        "reset", help="delete the practice database and set it up again from scratch"
    )
    reset.add_argument("--yes", action="store_true", help="don't ask first")
    commands.add_parser("backup", help="back up DataLab's database now")
    back = commands.add_parser(
        "rollback", help="restore the database from a backup, for an older DataLab"
    )
    back.add_argument("--list", action="store_true", help="list the backups and stop")
    back.add_argument("--backup", metavar="NAME", help="the backup to restore (default: newest)")
    back.add_argument(
        "--yes", action="store_true", help="restore even though it drops what was recorded since"
    )
    github = commands.add_parser("github", help="sign in to GitHub for the lab's repositories")
    github_commands = github.add_subparsers(dest="github_command", required=True)
    github_sign_in = github_commands.add_parser(
        "sign-in", help="sign in with a code at github.com/login/device"
    )
    github_sign_in.add_argument("--no-browser", action="store_true", help="don't open the page")
    github_sign_in.add_argument(
        "--again", action="store_true", help="sign in again even if already signed in"
    )
    repos = commands.add_parser("repos", help="the lab's repositories")
    repos_commands = repos.add_subparsers(dest="repos_command", required=True)
    repos_commands.add_parser("sync", help="clone or update the knowledge base and pipelines")
    versions = commands.add_parser(
        "versions", help="the DataLab versions installed side by side, and which one opens"
    )
    versions.add_argument("--use", metavar="VERSION", help="open this version from now on")
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

    if args.command == "practice-db":
        return _practice_db(settings, args)

    if args.command == "backup":
        return _backup(settings)

    if args.command == "rollback":
        return _rollback(settings, args)

    if args.command == "github":
        return _github_sign_in(settings, args)

    if args.command == "repos":
        return _repos_sync(settings)

    if args.command == "versions":
        return _versions(settings, args.use)

    if args.command == "db-check":
        return _db_check(settings)

    if args.command == "safety-check":
        from datalab.trial import run_safety_check

        return asyncio.run(run_safety_check(settings, strict=args.strict))

    if args.command == "catalog":
        return _build_catalog(settings, args)
    return 2


def _console_never_fails() -> None:
    """On Windows, output to a pipe or a file uses the locale's encoding
    (cp1252), which has no "→" or "✓": print those as "?" rather than fail."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def _serve(settings, *, open_browser: bool) -> int:
    from datalab.trial import refuse_if_running

    # The data folder's lock, held until DataLab exits: nothing else may
    # change the database while an interrupted update is sorted out, or after.
    refuse_if_running(settings)
    if settings.profile == "practice":
        _save_practice_password(settings)
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
    practice_database = None
    if settings.profile == "practice":
        from datalab.practice_db import PracticeDatabase, PracticeDatabaseKeeper

        # Started (and set up, the first time) in the background once DataLab
        # is up; the pages say how it's going.
        practice_database = PracticeDatabaseKeeper(
            PracticeDatabase(lock_dir=settings.data_dir / "practice-db")
        )
    try:
        app = create_app(
            settings,
            browser=browser,
            web_dist=_web_dist(),
            recovery=recovery,
            practice_database=practice_database,
        )
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
    _refresh_launcher_icons()
    url = f"http://{settings.host}:{settings.port}{browser.sign_in_path()}"
    print(f"DataLab ({settings.profile}) is starting. Open: {url}", flush=True)
    if open_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    # Asks GitHub for a newer release in the background, if allowed; being
    # offline or rate-limited only shows in Settings → Updates.
    threading.Thread(
        target=app.state.update_checker.check_on_start, name="update-check", daemon=True
    ).start()
    # On Windows, a policy can stop Docker's virtual machine from starting;
    # this says so in DataLab's window and offers to fix it, while DataLab runs.
    threading.Thread(target=_check_windows_vm, name="windows-vm-check", daemon=True).start()

    class Server(uvicorn.Server):
        async def serve(self, sockets=None) -> None:
            ignore_windows_connection_resets(asyncio.get_running_loop())
            await super().serve(sockets)

    # Open event streams from browser tabs never end on their own, so give
    # shutdown a few seconds, then stop sessions and containers regardless.
    server = Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level="warning",
            timeout_graceful_shutdown=5,
        )
    )

    def shutdown() -> None:
        # The updater restarts DataLab by quitting it (after starting the
        # helper that opens the new version).
        server.should_exit = True

    app.state.shutdown = shutdown
    server.run()
    return 0


def _check_windows_vm() -> None:
    import logging

    from datalab import windows_vm

    try:
        windows_vm.check_before_serve(say=lambda text: print(text, flush=True))
    except Exception:  # never a reason for DataLab to stop
        logging.getLogger(__name__).exception("couldn't check Docker's virtual machine")


def _refresh_launcher_icons() -> None:
    """An installed DataLab keeps its launcher's icon its own package's (an
    update from a version before this one never changed it). A copy not run
    from the installer's layout (a checkout, tests) leaves launchers alone."""
    import logging

    from datalab import launcher_icons
    from datalab.updater import Layout, default_root

    try:
        layout = Layout(default_root())
        if layout.running_version() is not None:
            launcher_icons.refresh(launcher_icons.package_branding(), root=layout.root)
    except Exception:  # never a reason not to start
        logging.getLogger(__name__).exception("couldn't refresh the launcher's icon")


def _save_practice_password(settings) -> None:
    """A practice DataLab set up before `datalab setup` saved its synthetic
    database's password (0.1.0) saves it now. Never for the real profile."""
    from datalab.setup import save_practice_password

    try:
        save_practice_password(settings)
    except Exception as error:  # Windows' keychain raises its own errors, too
        # Best effort: the Safety check and Settings say what's missing. The
        # error's type only, never its text.
        print(
            f"Couldn't save the practice database's password ({type(error).__name__}).",
            flush=True,
        )


def ignore_windows_connection_resets(loop: asyncio.AbstractEventLoop) -> None:
    """Don't report the reset Windows' asyncio transport raises when a
    browser's connection is already gone (WinError 10054), typically when
    DataLab stops. It's asyncio's own noise, not a problem; every other
    error is reported as before."""
    report = loop.get_exception_handler()

    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        callback = repr(context.get("handle") or context.get("message") or "")
        if (
            isinstance(context.get("exception"), ConnectionResetError)
            and "_ProactorBasePipeTransport._call_connection_lost" in callback
        ):
            return
        if report is not None:
            report(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


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
        pulled = subprocess.run(
            ["docker", "pull", "-q", image],
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
        if pulled.returncode != 0:
            error = pulled.stderr.strip()
            print(error)
            print(f"Couldn't download {image}.")
            print(_pull_failure_hint(error))
            return 1
    if settings.profile == "practice":
        return _pull_practice_database_image()
    return 0


def _pull_practice_database_image() -> int:
    """Practice only: Oracle Database Free, for the synthetic database, from
    Oracle's registry (tried again a few times when it's busy). Only for a
    first setup: an existing practice database already has what it needs,
    so an update never downloads it again. Never a reason to stop an
    install or an update either: the practice database's setup downloads it
    when it creates the container."""
    from datalab import practice_db

    try:
        database = practice_db.PracticeDatabase()
        if not database.image_needed():
            print("Oracle Database Free (the practice database): nothing to download.")
            return 0
        database.pull(say=lambda text: print(text, flush=True), show=True)
    except ValueError as problem:
        print(problem)
    except practice_db.PracticeDatabaseProblem as problem:
        print(problem)
        print(
            "Carrying on: setting up the practice database downloads it, and practice "
            "DataLab tries again each time it opens."
        )
    return 0


def _practice_db(settings, args) -> int:
    """`datalab --profile practice practice-db ...`. Never for the real profile."""
    import oracledb

    from datalab import practice_db
    from datalab.practice_db.guard import NotTheSyntheticDatabase

    if settings.profile != "practice":
        print(
            "practice-db is for the practice DataLab's synthetic database only. Run: "
            f"datalab --profile practice practice-db {args.practice_db_command}"
        )
        return 2

    def say(text: str) -> None:
        print(text, flush=True)

    def use_legacy(name: str) -> bool:
        if not sys.stdin.isatty():
            return True
        print(
            f"The development synthetic database ({name}) is on this computer, stopped, and "
            "uses the practice database's port."
        )
        answer = input("Start it and use it, rather than set up a second one? [Y/n] ")
        return answer.strip().lower() in ("", "y", "yes")

    command = args.practice_db_command
    try:
        database = practice_db.PracticeDatabase(
            lock_dir=settings.data_dir / "practice-db", use_legacy=use_legacy
        )
        if command == "status":
            say(database.describe())
            return 0
        if command == "stop":
            say(database.stop())
            return 0
        if command in ("setup", "start"):
            if _practice_running(settings):
                say(
                    "Practice DataLab is running, and it looks after its database itself: "
                    "Settings → Connections says how it stands."
                )
                return 0
            say(database.ensure(say, show_download=True).message)
            return 0
        # reset: a running practice DataLab would be querying what's removed.
        from datalab.trial import refuse_if_running

        refuse_if_running(settings)
        if not args.yes:
            print(
                "This deletes the practice database (the container "
                f"{database.target.container} and its volume {database.target.volume}) and sets "
                "it up again from scratch, with the same made-up data. Your practice "
                "conversations, results and exports aren't touched."
            )
            if input("Reset it? [y/N] ").strip().lower() not in ("y", "yes"):
                say("Nothing was changed.")
                return 1
        say(database.reset(say, show_download=True).message)
        return 0
    except (practice_db.PracticeDatabaseProblem, NotTheSyntheticDatabase, ValueError) as problem:
        say(str(problem))
        return 1
    except oracledb.DatabaseError as error:
        # Only the error's code: the practice database's own, never a secret.
        code = str(error).split(":", 1)[0].strip()[:20]
        say(
            f"The practice database refused a step ({code}). Try again; if it happens "
            "again, reset it."
        )
        return 1


def _practice_running(settings) -> bool:
    """Whether practice DataLab is running on this data folder (or its port)."""
    import socket

    from datalab import datalock

    if datalock.in_use(settings.data_dir):
        return True
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", settings.port)) == 0


def _pull_failure_hint(error: str) -> str:
    """What to do about a failed `docker pull`, in words for someone non-technical."""
    lowered = error.lower()
    # Docker's own engine first: its pipe (Windows) or socket (Mac, Linux) is
    # named in the message. Otherwise "access is denied" on the Windows pipe
    # (not in docker-users) or "permission denied ... daemon socket" would look
    # like the registry refusing the image.
    local_engine = re.search(
        r"//\./pipe/|%2fpipe%2f|docker\.sock|daemon socket|docker daemon", lowered
    )
    if local_engine and ("access is denied" in lowered or "permission denied" in lowered):
        return (
            "This account isn't allowed to use Docker yet. On Windows, it has to be in the "
            "'docker-users' group, which only takes effect after a restart (or signing out and "
            "in again). Restart, then try again; if it still happens, ask IT to add your "
            "account to 'docker-users' on this computer."
        )
    if (
        local_engine
        or "cannot connect" in lowered
        or ("daemon" in lowered and "running" in lowered)
    ):
        return (
            "Docker Desktop doesn't seem to be running. Start it, wait until it says "
            "'Engine running', then try again."
        )
    if "unauthorized" in lowered or "denied" in lowered:
        return (
            "The registry wouldn't let this computer download it. That's not something you "
            "did: the DataLab maintainer needs to make the image available. Send them this message."
        )
    return "Check the internet connection (and the VPN, if you're on one), then try again."


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


def _github_auth(settings):
    from datalab.repos.github import GitHubAuth

    return GitHubAuth(settings.repos.client_id) if settings.repos.client_id else None


def _github_sign_in(settings, args) -> int:
    from datalab.repos import commands

    return commands.sign_in(
        settings, _github_auth(settings), open_browser=not args.no_browser, again=args.again
    )


def _repos_sync(settings) -> int:
    from datalab.repos import commands

    auth = _github_auth(settings)
    if commands.why_not(settings, auth) is None:
        from datalab.datalock import refuse_second_instance

        # The clones and their record belong to the DataLab using this folder.
        refuse_second_instance(settings.data_dir, settings.profile)
    return commands.sync(settings, auth)


def _versions(settings, use: str | None) -> int:
    from datalab import __version__
    from datalab.updater import Layout, UpdateFailed, default_root

    layout = Layout(default_root())
    current, previous = layout.pointer()
    installed = layout.installed()
    if use is None:
        if not installed:
            print(f"No versions installed side by side in {layout.versions}.")
            print(f"This is DataLab {__version__}, running from {sys.prefix}.")
            return 0
        for version in reversed(installed):
            notes = [
                label
                for label, applies in (
                    ("opens from the launcher", version == current),
                    ("the one before", version == previous),
                    ("running this command", version == layout.running_version()),
                )
                if applies
            ]
            print(f"{version:<16} {', '.join(notes)}")
        return 0
    import os

    from datalab import updates
    from datalab.config import default_data_dir
    from datalab.releases import parse_version

    # An update under way (in either profile's data folder) is the startup
    # recovery's to sort out, with the launcher as the update left it.
    folders = {settings.data_dir}
    if not os.environ.get("DATALAB_DATA_DIR"):
        folders |= {default_data_dir("real"), default_data_dir("practice")}
    for folder in sorted(folders):
        try:
            marker = updates.read_marker(folder)
        except updates.UnreadableMarker:
            print(f"An update left a note in {folder} that DataLab couldn't read. Open DataLab")
            print("once so it can sort that out, then try again.")
            return 1
        if marker is not None:
            print(
                f"An update from {marker.from_version} to {marker.to_version} hasn't finished "
                f"({folder}). Open DataLab once so it can sort that out, then try again."
            )
            return 1
    parsed = parse_version(use)
    version = str(parsed) if parsed else use
    if version not in installed:
        print(f"DataLab {use} isn't installed here. Installed: {', '.join(installed) or 'none'}.")
        return 1
    if version == current:
        print(f"DataLab {version} is already the one the launcher opens.")
        return 0
    try:
        layout.switch(version, previous=current)
    except UpdateFailed as error:
        print(error)
        return 1
    print(f"The launcher opens DataLab {version} from now on (it opened {current}).")
    print(
        "If that version says the database is newer than it, go back with "
        f"datalab versions --use {current}, or run datalab rollback (with {version})."
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
