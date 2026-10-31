#!/usr/bin/env python3
"""One-off: merge iOS simulators' `price_snapshots` tables into the archive's line format.

    python3 tools/seed_from_simulators.py <out.ndjson.gz> <tromi.db> [<tromi.db> ...]

A simulator keeps what it last fetched until it next refreshes, so its database is a
frozen window from whenever the app last ran there. Several databases overlap, so exact
duplicates are dropped. A repeated price at another time stays: it's another sighting.
"""
import datetime as dt
import gzip
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collect  # noqa: E402

# iOS stores its enum's raw values; the archive uses the app-wide names.
FUELS = {"regulier": "REGULIER", "super_": "SUPER", "diesel": "DIESEL"}


def stamp(captured_at):
    moment = dt.datetime.fromtimestamp(captured_at / 1000, dt.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def rows_from(db_path):
    query = "select stationId, fuelType, priceCents, capturedAt from price_snapshots"
    for station, fuel, cents, captured_at in sqlite3.connect(db_path).execute(query):
        yield stamp(captured_at), station, FUELS[fuel], round(cents * 10)


def main(out_path, db_paths):
    rows = set()
    for db_path in db_paths:
        rows.update(rows_from(db_path))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as out:
        for moment, station, fuel, cents in sorted(rows):
            out.write(collect.change_line(moment, f"{station}|{fuel}", cents) + "\n")
    print(f"Wrote {len(rows)} rows from {len(db_paths)} databases to {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
