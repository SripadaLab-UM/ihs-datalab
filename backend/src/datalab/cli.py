"""`datalab` command line: run the app and a few maintainer tasks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from datalab.config import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="datalab")
    parser.add_argument("--profile", choices=["real", "practice"], help="default: real")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("serve", help="run DataLab")
    commands.add_parser("db-check", help="connect and report what the session may do")

    catalog = commands.add_parser("catalog", help="build the schema catalog (metadata only)")
    catalog.add_argument("--out", type=Path, required=True, help="catalog folder to write")
    source = catalog.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-export", type=Path, help="a CSV metadata export folder")
    source.add_argument("--from-database", action="store_true", help="read the configured database")

    args = parser.parse_args(argv)
    settings = load_settings(args.profile)

    if args.command == "serve":
        import uvicorn

        from datalab.app import create_app

        uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
        return 0

    if args.command == "db-check":
        return _db_check(settings)

    if args.command == "catalog":
        return _build_catalog(settings, args)
    return 2


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
