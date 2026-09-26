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

    args = parser.parse_args(argv)
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

    refuse_if_running(settings)
    import threading
    import webbrowser

    import uvicorn

    from datalab.app import create_app
    from datalab.web import BrowserSession

    browser = BrowserSession()
    app = create_app(settings, browser=browser, web_dist=_web_dist())
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
