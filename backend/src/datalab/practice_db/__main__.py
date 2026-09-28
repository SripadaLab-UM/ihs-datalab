"""The synthetic database for development and CI: `synthetic/db.sh`, which
runs `python -m datalab.practice_db <command>` with the backend's
environment. The same code practice DataLab uses (datalab.practice_db), for
the development container: datalab-synthetic-oracle on 127.0.0.1:1522,
unless DATALAB_PRACTICE_DB_CONTAINER, _VOLUME or _PORT say otherwise.

    start      create or start the container and wait until it's ready
    stop       stop it (its data is kept)
    reset      delete the container and its volume, then start a fresh one
    status     the container's state
    generate   start, then (re)load the synthetic data: drop and recreate
    setup      what practice DataLab does: start, and load the data only if it has none
    verify     check privileges, row counts and quirks as DATALAB_RO
"""

from __future__ import annotations

import argparse
import os
import sys

from datalab.practice_db import PracticeDatabase, PracticeDatabaseProblem, Target
from datalab.practice_db.guard import NotTheSyntheticDatabase

DEV_CONTAINER = "datalab-synthetic-oracle"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m datalab.practice_db", description=__doc__)
    parser.add_argument(
        "command", choices=["start", "stop", "reset", "status", "generate", "setup", "verify"]
    )
    parser.add_argument("--seed", type=int, help="generate: another seed than the spec's")
    args = parser.parse_args(argv)
    from datalab.config import PRACTICE_ORACLE

    container = os.environ.get("DATALAB_PRACTICE_DB_CONTAINER") or DEV_CONTAINER
    target = Target(
        container=container,
        volume=os.environ.get("DATALAB_PRACTICE_DB_VOLUME") or f"{container}-data",
        port=PRACTICE_ORACLE.port,
    )
    # The development container from before DataLab labelled its own is looked after too.
    database = PracticeDatabase(target, manage_unlabelled=True)
    say = lambda text: print(text, flush=True)  # noqa: E731
    try:
        if args.command == "status":
            say(database.describe())
        elif args.command == "stop":
            say(database.stop())
        elif args.command in ("start", "generate"):
            if not database.up(say, show_download=True):
                say(f"Something else answers on 127.0.0.1:{target.port}; left alone.")
                return 1
            say(f"ready: 127.0.0.1:{target.port}/FREEPDB1")
            if args.command == "generate":
                from datalab.practice_db.generate import build

                build(target.dsn, seed=args.seed, say=say)
        elif args.command == "reset":
            database.remove(volume=True)
            database.up(say, show_download=True)
            say(f"ready: 127.0.0.1:{target.port}/FREEPDB1 (no data yet: run generate)")
        elif args.command == "setup":
            say(database.ensure(say, show_download=True))
        elif args.command == "verify":
            from datalab.practice_db.verify import verify

            return 1 if verify(target.dsn, say=say) else 0
    except (PracticeDatabaseProblem, NotTheSyntheticDatabase) as problem:
        print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
