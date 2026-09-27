"""Make a fake daily-wearables table, shaped like an IHS cohort view.

Everything here is invented: participant IDs are SYN-####, and every value
comes from a seeded random generator. Nothing is read from any database.

`--as-of` stops the data at a date, to imitate an ongoing cohort: a later
date gives more rows, which is what "Run again" should pick up and "Replay"
should not.

    python make_fake_data.py out.csv [--as-of 2025-05-31]
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta

DEVICES = [("fitbit", 25), ("apple", 12), ("garmin", 3)]  # garmin is a small cell
START = date(2025, 4, 1)


def rows(as_of: date, seed: int = 20260927):
    rng = random.Random(seed)
    number = 0
    for device, count in DEVICES:
        for _ in range(count):
            number += 1
            pid = f"SYN-{number:04d}"
            base_steps = rng.randint(3000, 12000)
            base_hr = rng.randint(55, 75)
            day = START
            while day <= as_of:
                missing = rng.random() < 0.08
                yield {
                    "STUDY_PARTICIPANT_ID": pid,
                    "RECORD_DATE": f"{day.isoformat()} 00:00:00",  # as str(datetime) from oracledb
                    "DEVICE": device,
                    "STEPS": "" if missing else max(0, int(rng.gauss(base_steps, 2500))),
                    "RESTING_HR": "" if rng.random() < 0.05 else base_hr + rng.randint(-4, 4),
                    "SLEEP_MINUTES": "" if rng.random() < 0.1 else int(rng.gauss(420, 50)),
                }
                day += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--as-of", default="2025-05-31")
    args = parser.parse_args()
    with open(args.out, "w", newline="", encoding="utf-8") as handle:
        writer = None
        for row in rows(date.fromisoformat(args.as_of)):
            if writer is None:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)


if __name__ == "__main__":
    main()
