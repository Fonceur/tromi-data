#!/usr/bin/env python3
"""One-off: turn a phone's `price_snapshots` table into the archive's line format.

    python3 tools/seed_from_phone.py <tromi.db> history/seed/<name>.ndjson.gz

These rows are phone-sampled, not collected: they were captured whenever the app
happened to refresh, so a change is timed to that refresh, and a station the phone
pruned is missing. They live under history/seed/ and never mix with the daily files.
"""
import datetime as dt
import gzip
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collect  # noqa: E402


def main(db_path, out_path):
    rows = sqlite3.connect(db_path).execute(
        "select stationId, fuelType, priceCents, capturedAt from price_snapshots "
        "order by capturedAt, stationId, fuelType"
    )
    count = 0
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as out:
        for station, fuel, cents, captured_at in rows:
            stamp = dt.datetime.fromtimestamp(captured_at / 1000, dt.timezone.utc)
            line = collect.change_line(stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), f"{station}|{fuel}",
                                       round(cents * 10))
            out.write(line + "\n")
            count += 1
    print(f"Wrote {count} rows to {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
