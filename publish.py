#!/usr/bin/env python3
"""Build the file Tromi backfills its price history from: the last 30 days of changes.

Writes `_site/data/qc/prices-30d.json`, which the workflow deploys to GitHub Pages:

    {"schema": 1, "source": "Régie essence Québec, Régie de l'énergie", ..., "from": <epoch s>,
     "to": <epoch s>, "stations": {"<id>": {"REGULIER": [[<epoch s>, 1994], ...]}}}

Each pair is a price the Régie published, as published (tenths of a cent), stamped
with the feed time it was first seen at. Rows are kept only where the price differs
from the one before it, which is how the app's `price_snapshots` table is written,
so the file inserts there as is. Nothing is averaged, rounded or filled in.

Sources: the collector's daily files, plus `history/seed/` for the time before
collection began, when only test devices recorded prices.
"""
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = Path("_site") / "data" / "qc" / "prices-30d.json"

SOURCE = "Régie essence Québec, Régie de l'énergie"
SOURCE_URL = "https://regieessencequebec.ca"
TERMS = (
    "Prices as published by Régie essence Québec (Régie de l'énergie), not modified. "
    "The Régie allows redistribution on three conditions: the data is not modified; the "
    "source, \"Régie essence Québec, Régie de l'énergie\", is credited and visible at all "
    "times; and nobody claims, or lets anyone believe, they are mandated by the Régie de "
    "l'énergie. Tromi is independent of the Régie. Station ids are Tromi's, derived from "
    "the address and postal code. See https://github.com/Fonceur/tromi-data#source-and-terms"
)

# Bumped only for a change an older app can't read; the app rejects any other value.
SCHEMA = 1
WINDOW_S = 30 * 24 * 3600
# How far before the window to read, so a row in it can be told apart from a repeat
# of an older price. The collector writes only changes, so a month is plenty.
LOOKBACK_S = 30 * 24 * 3600
# The collector's first baseline. Seed rows only fill in the time before it: after
# it, the collector saw every feed the devices could have.
COLLECTION_START = "2026-10-31T02:30:00Z"
# Fewer stations than this means the archive, not the province, went wrong.
MIN_STATIONS = 2000


def epoch(stamp):
    return int(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp())


def read_lines(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                yield json.loads(line)


def stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_rows(root, since=""):
    """Archived rows from [since] on, as (t, source order, id, fuel, cents), oldest first.

    Seed rows come first at an equal stamp, so the collector's reading wins a tie.
    Daily files are picked by name, so the archive's age doesn't slow the build.
    """
    rows = []
    if since < COLLECTION_START:
        for order, path in enumerate(sorted((root / "history" / "seed").glob("*.ndjson.gz"))):
            rows += [(r["t"], order, r["id"], r["f"], r["c"])
                     for r in read_lines(path) if since <= r["t"] < COLLECTION_START]
    late = len(rows) + 1_000_000  # any order above every seed file's
    first_day = since[:10]
    for path in sorted((root / "history").glob("[0-9][0-9][0-9][0-9]/*/*.ndjson")):
        day = f"{path.parent.parent.name}-{path.parent.name}-{path.stem}"
        if day >= first_day:
            rows += [(r["t"], late, r["id"], r["f"], r["c"]) for r in read_lines(path)]
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def build(rows, now_stamp):
    """The 30 days up to [now_stamp], as {id: {fuel: [[epoch s, cents], ...]}}.

    `null` (a grade or station that left the feed) is skipped, not stored: the app
    keeps showing a station's last price too, and doesn't record a gap.
    """
    end = epoch(now_stamp)
    start = end - WINDOW_S
    last = {}
    stations = {}
    for t, _, sid, fuel, cents in rows:
        if cents is None:
            continue
        at = epoch(t)
        if at > end:
            break
        if last.get((sid, fuel)) == cents:
            continue
        last[(sid, fuel)] = cents
        if at >= start:
            stations.setdefault(sid, {}).setdefault(fuel, []).append([at, cents])
    return start, end, stations


def document(start, end, stations):
    return {
        "schema": SCHEMA,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "terms": TERMS,
        "from": start,
        "to": end,
        "unit": "tenths of a cent: 1994 is 199.4¢",
        "rows": "per station and grade, [epoch seconds, price], oldest first; each is a "
                "published price, stamped with the feed time it was first seen at, and "
                "kept only where it differs from the row before it",
        "stations": stations,
    }


def main():
    now_stamp = json.loads((ROOT / "state" / "feed.json").read_text(encoding="utf-8"))["generated_at"]
    rows = load_rows(ROOT, since=stamp(epoch(now_stamp) - WINDOW_S - LOOKBACK_S))
    start, end, stations = build(rows, now_stamp)
    if len(stations) < MIN_STATIONS:
        # Exits non-zero, so the deploy step never runs and Pages keeps the last good file.
        print(f"::error::Not published: only {len(stations)} stations (floor {MIN_STATIONS}).")
        return 1
    out = ROOT / OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document(start, end, stations), ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True)
    out.write_text(text, encoding="utf-8")
    pairs = sum(len(f) for f in stations.values())
    count = sum(len(r) for f in stations.values() for r in f.values())
    print(f"{OUT}: {len(stations)} stations, {pairs} station-grade pairs, {count} rows, "
          f"{len(text.encode()) // 1024} KB, up to {now_stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
