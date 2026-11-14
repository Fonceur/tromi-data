#!/usr/bin/env python3
"""Build the two files the workflow deploys to GitHub Pages.

`_site/data/qc/prices-30d.json`, the file Tromi backfills its price history from, holds the
last 30 days of changes:

    {"schema": 1, "source": "Régie essence Québec, Régie de l'énergie", ..., "from": <epoch s>,
     "to": <epoch s>, "stations": {"<id>": {"REGULIER": [[<epoch s>, 1994], ...]}}}

Each pair is a price the Régie published, as published (tenths of a cent), stamped
with the feed time it was first seen at. Rows are kept only where the price differs
from the one before it, which is how the app's `price_snapshots` table is written,
so the file inserts there as is. Nothing is averaged, rounded or filled in.

`_site/data/qc/regional-daily.json` is Tromi's own calculation from those prices, not
the Régie's: a daily average per Régie region and grade over the 30 complete Québec days
before the last feed, each point with the number of stations it averages:

    {"schema": 1, ..., "from": "2026-10-09", "to": "2026-11-07",
     "regions": {"Montréal": {"REGULIER": [["2026-10-09", 1939, 223], ...]}}}

`_site/data/qc/history/prices-YYYY-MM.json` holds one Québec month of changes, in the
30-day file's rows, plus `starts`, the price in force at the month's `from` for each
station and grade where it's known. `_site/data/qc/history/index.json` names the months
and the first moment from which the archive holds prices with no gap of more than a day:

    {"schema": 1, ..., "from": <epoch s>, "to": <epoch s>, "months": ["2026-09", ...]}

Tromi for iOS reads them to draw a price chart over any days since `from` (W171).

Sources: the collector's daily files, plus `history/seed/` for the time before
collection began, when only test devices recorded prices.
"""
import gzip
import json
import sys
from functools import lru_cache
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUT = Path("_site") / "data" / "qc" / "prices-30d.json"
REGIONAL_OUT = Path("_site") / "data" / "qc" / "regional-daily.json"
HISTORY_OUT = Path("_site") / "data" / "qc" / "history"

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
# The regional file's own number: bumping SCHEMA for it would break every app's backfill.
REGIONAL_SCHEMA = 1
WINDOW_S = 30 * 24 * 3600
# How far before the window to read, so a row in it can be told apart from a repeat
# of an older price. The collector writes only changes, so a month is plenty.
LOOKBACK_S = 30 * 24 * 3600
# The collector's first baseline. Seed rows only fill in the time before it: after
# it, the collector saw every feed the devices could have.
COLLECTION_START = "2026-10-31T02:30:00Z"
# Fewer stations than this means the archive, not the province, went wrong.
MIN_STATIONS = 2000

REGIONAL_TERMS = (
    "Averages calculated by Tromi from the prices published by Régie essence Québec "
    "(Régie de l'énergie). They are Tromi's figures, not the Régie's: the Régie publishes "
    "regional averages of its own, which these can differ from, and nothing here may be "
    "presented as one of them. Wherever these figures are shown, the source, \"Régie "
    "essence Québec, Régie de l'énergie\", must be credited and visible, and they must be "
    "labelled as Tromi's calculation. Tromi is independent of the Régie and isn't mandated "
    "by it. See https://github.com/Fonceur/tromi-data#source-and-terms"
)
REGIONAL_METHOD = (
    "For each day, Québec time, each station's last published price for the grade at the "
    "end of that day, averaged over the region's stations that had one, and rounded half up "
    "to a tenth of a cent. A station counts in the region the current feed gives it, so a "
    "station that has left the feed counts on no day. A point is left out when fewer than "
    "5 stations had a price, or fewer than 80% of the number on the series' last day."
)
# Québec's clock; America/Montreal is only a link to this zone.
QUEBEC_TIME = ZoneInfo("America/Toronto")
REGIONAL_DAYS = 30
# An average over fewer stations reads as one station's price, not a region's.
MIN_STATIONS_PER_POINT = 5
# A day that knows far fewer stations than the last one averages a different set, so
# the line would jump with coverage, not with prices (the seed's earliest days).
MIN_COVERAGE = 0.8
# Fewer regions with a regular-grade line than this means the station-to-region join
# broke (17 regions qualified on 2026-11-08), so nothing is published.
MIN_REGIONS = 15

# The month files' and the index's own number, as the regional file has its own.
HISTORY_SCHEMA = 1
# The archive is continuous back to the first recorded refresh after a gap longer than
# this. Prices change several times a day, so a longer gap is history lost. Measured on
# 2026-11-13: a day reaches back to 2026-10-01T02:15:58Z (Sept 30, 22:15 in Québec), the
# owner's choice; 12 hours would stop at Oct 30, after the seed's 14.6-hour gaps.
MAX_GAP_S = 24 * 3600


@lru_cache(maxsize=None)  # a few thousand distinct stamps, each parsed once
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


def regional_days(now_stamp):
    """The REGIONAL_DAYS complete Québec days before the one [now_stamp] falls on."""
    today = datetime.fromtimestamp(epoch(now_stamp), QUEBEC_TIME).date()
    return [today - timedelta(days=REGIONAL_DAYS - i) for i in range(REGIONAL_DAYS)]


def day_end(day):
    """The UTC stamp of the Québec midnight that ends [day] (DST days are 23 or 25 h)."""
    return stamp(int(datetime.combine(day + timedelta(days=1), time(), QUEBEC_TIME).timestamp()))


def regional_since(now_stamp):
    """Where the regional build starts reading. Unlike the backfill file, it needs every
    current price, and one that hasn't changed since the collector's first run is known
    only from that run's full snapshot, so it never starts later than COLLECTION_START.
    Before that, the seed is read from a month ahead of the window, as for the backfill.
    """
    opening = day_end(regional_days(now_stamp)[0] - timedelta(days=1))
    return min(stamp(epoch(opening) - LOOKBACK_S), COLLECTION_START)


def build_regional(rows, now_stamp, regions):
    """{region: {fuel: [[day, average, stations], ...]}} for the days before [now_stamp].

    [rows] must start at regional_since(now_stamp). [regions] maps a station id to its
    Régie region. A row stamped at a day's closing midnight belongs to the next day: the
    feed saw it then, not before.
    """
    days = regional_days(now_stamp)
    close = {}
    series = {}
    i = 0

    def advance(until):
        nonlocal i
        while i < len(rows) and rows[i][0] < until:
            _, _, sid, fuel, cents = rows[i]
            close[(sid, fuel)] = cents  # null: the grade or station left the feed
            i += 1

    collecting = False
    for day in days:
        end = day_end(day)
        if not collecting and end > COLLECTION_START:
            advance(COLLECTION_START)
            # The collector's first run wrote the whole feed, but no null for a price only
            # the seed knew, such as a grade a station had stopped selling before then.
            close.clear()
            collecting = True
        advance(end)
        totals = {}
        for (sid, fuel), cents in close.items():
            region = regions.get(sid)
            if cents is not None and region:
                total = totals.setdefault((region, fuel), [0, 0])
                total[0] += cents
                total[1] += 1
        for key, (total, n) in totals.items():
            # Half up in integers: Python's round() would send 1994.5 to 1994.
            series.setdefault(key, []).append([day.isoformat(), (2 * total + n) // (2 * n), n])
    out = {}
    for (region, fuel), points in sorted(series.items()):
        if points[-1][0] != days[-1].isoformat():
            continue  # no price on the last day: the series has no reference count
        floor = max(MIN_STATIONS_PER_POINT, MIN_COVERAGE * points[-1][2])
        kept = [p for p in points if p[2] >= floor]
        if kept:
            out.setdefault(region, {})[fuel] = kept
    return days[0], days[-1], out


def regional_document(first, last, feed_at, regions):
    return {
        "schema": REGIONAL_SCHEMA,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "terms": REGIONAL_TERMS,
        "method": REGIONAL_METHOD,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "feed": feed_at,
        "time_zone": "America/Toronto",
        "unit": "tenths of a cent: 1994 is 199.4¢",
        "points": "per Régie region and grade, [day, average price, stations averaged], "
                  "oldest first; a day missing from a series was left out (see method)",
        "regions": regions,
    }


def regional_problem(regions):
    """Why the regional file must not be published, or None."""
    lines = sum(1 for fuels in regions.values() if "REGULIER" in fuels)
    if lines < MIN_REGIONS:
        return f"only {lines} regions have a regular-grade line (floor {MIN_REGIONS})"
    return None


def continuous_from(rows, end):
    """The first recorded refresh, at or before [end] (epoch s), from which no two
    refreshes are more than MAX_GAP_S apart. A refresh is a distinct row stamp."""
    stamps = sorted({epoch(r[0]) for r in rows if epoch(r[0]) <= end})
    if not stamps:
        return end
    i = len(stamps) - 1
    while i > 0 and stamps[i] - stamps[i - 1] <= MAX_GAP_S:
        i -= 1
    return stamps[i]


def month_start(year, month):
    """Québec midnight on the month's first day, as epoch seconds."""
    return int(datetime(year, month, 1, tzinfo=QUEBEC_TIME).timestamp())


def history_months(start, end):
    """Each Québec month from the one [start] falls in to the one [end] falls in, as
    (key, from, to): the month's own bounds, held to [start] and [end]."""
    first = datetime.fromtimestamp(start, QUEBEC_TIME)
    last = datetime.fromtimestamp(end, QUEBEC_TIME)
    year, month = first.year, first.month
    months = []
    while (year, month) <= (last.year, last.month):
        following = (year + 1, 1) if month == 12 else (year, month + 1)
        months.append((f"{year:04d}-{month:02d}",
                       max(start, month_start(year, month)), min(end, month_start(*following))))
        year, month = following
    return months


def build_months(rows, start, end):
    """{key: (from, to, starts, stations)} for each month from [start] to [end].

    `stations` holds the month's changes as `build` writes them: a row only where the
    price differs from the last one seen, `null` skipped. A pair the month doesn't know
    yet (no start, no row) gets its first price as a row even when it repeats the last
    one seen, as after a `null`, so every month can be drawn on its own. `starts` holds the price in
    force at the month's `from`, for each station and grade that has one: a `null`
    takes a pair out until it's priced again. Rows before [start] are left out, since
    the archive has gaps there. At the collector's first run, every price is forgotten,
    as the regional build does: that run wrote the whole feed, but no `null` for a
    price only the seed knew.
    """
    months = history_months(start, end)
    out = {key: (lo, hi, {}, {}) for key, lo, hi in months}
    seen = {}      # the last price, for telling a change from a repeat, as `build`
    current = {}   # the price in force, None once a pair left the feed
    known = set()  # the pairs the month being built has a start or a row for
    collecting = start >= epoch(COLLECTION_START)
    m = 0

    def open_month(index):
        _, _, starts, _ = out[months[index][0]]
        known.clear()
        for (sid, fuel), cents in current.items():
            if cents is not None:
                starts.setdefault(sid, {})[fuel] = cents
                known.add((sid, fuel))

    if months:
        open_month(0)
    for t, _, sid, fuel, cents in rows:
        at = epoch(t)
        if at < start:
            continue
        if at > end:
            break
        if not collecting and t >= COLLECTION_START:
            seen.clear()
            current.clear()
            collecting = True
        while m + 1 < len(months) and at >= months[m + 1][1]:
            m += 1
            open_month(m)
        current[(sid, fuel)] = cents
        if cents is None or (seen.get((sid, fuel)) == cents and (sid, fuel) in known):
            continue
        seen[(sid, fuel)] = cents
        known.add((sid, fuel))
        out[months[m][0]][3].setdefault(sid, {}).setdefault(fuel, []).append([at, cents])
    while m + 1 < len(months):
        m += 1
        open_month(m)
    return out


def month_document(key, start, end, starts, stations):
    return {
        "schema": HISTORY_SCHEMA,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "terms": TERMS,
        "month": key,
        "time_zone": "America/Toronto",
        "from": start,
        "to": end,
        "unit": "tenths of a cent: 1994 is 199.4¢",
        "starts": starts,
        "start": "per station and grade, the published price in force at `from`, where known",
        "rows": "per station and grade, [epoch seconds, price], oldest first; each is a "
                "published price, stamped with the feed time it was first seen at, and "
                "kept only where it differs from the row before it",
        "stations": stations,
    }


def history_index(start, end, keys):
    return {
        "schema": HISTORY_SCHEMA,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "terms": TERMS,
        "from": start,
        "to": end,
        "time_zone": "America/Toronto",
        "continuity": f"from `from` on, no two recorded refreshes are more than "
                      f"{MAX_GAP_S // 3600} hours apart",
        "months": keys,
        "path": "prices-<month>.json, beside this file",
    }


def history_problem(months):
    """Why the month files must not be published, or None. A month the collector covered
    from its start knows every station, so few of them means the archive broke."""
    for key, (start, _, starts, stations) in months.items():
        if start < epoch(COLLECTION_START):
            continue
        known = set(starts) | set(stations)
        if len(known) < MIN_STATIONS:
            return f"{key} knows only {len(known)} stations (floor {MIN_STATIONS})"
    return None


def write_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    path.write_text(text, encoding="utf-8")
    return len(text.encode())


def main():
    now_stamp = json.loads((ROOT / "state" / "feed.json").read_text(encoding="utf-8"))["generated_at"]
    rows = load_rows(ROOT, since=stamp(epoch(now_stamp) - WINDOW_S - LOOKBACK_S))
    start, end, stations = build(rows, now_stamp)
    # Any failure exits non-zero before anything is written, so the deploy step never
    # runs and Pages keeps the last good copy of both files: a deploy replaces the site.
    if len(stations) < MIN_STATIONS:
        print(f"::error::Not published: only {len(stations)} stations (floor {MIN_STATIONS}).")
        return 1
    known = json.loads((ROOT / "state" / "stations.json").read_text(encoding="utf-8"))
    first, last, regions = build_regional(
        load_rows(ROOT, since=regional_since(now_stamp)), now_stamp,
        {sid: s.get("region") for sid, s in known.items()})
    problem = regional_problem(regions)
    if problem:
        print(f"::error::Not published: {problem}.")
        return 1
    archive = load_rows(ROOT)
    history_from = continuous_from(archive, end)
    months = build_months(archive, history_from, end)
    problem = history_problem(months)
    if problem:
        print(f"::error::Not published: {problem}.")
        return 1
    size = write_json(ROOT / OUT, document(start, end, stations))
    pairs = sum(len(f) for f in stations.values())
    count = sum(len(r) for f in stations.values() for r in f.values())
    print(f"{OUT}: {len(stations)} stations, {pairs} station-grade pairs, {count} rows, "
          f"{size // 1024} KB, up to {now_stamp}")
    size = write_json(ROOT / REGIONAL_OUT, regional_document(first, last, end, regions))
    lines = sum(len(f) for f in regions.values())
    print(f"{REGIONAL_OUT}: {len(regions)} regions, {lines} region-grade lines, "
          f"{first} to {last}, {size // 1024} KB")
    for key, (lo, hi, starts, changes) in months.items():
        size = write_json(ROOT / HISTORY_OUT / f"prices-{key}.json",
                          month_document(key, lo, hi, starts, changes))
        count = sum(len(r) for f in changes.values() for r in f.values())
        print(f"{HISTORY_OUT}/prices-{key}.json: {len(starts)} stations priced at its start, "
              f"{count} rows, {size // 1024} KB")
    write_json(ROOT / HISTORY_OUT / "index.json", history_index(history_from, end, list(months)))
    print(f"{HISTORY_OUT}/index.json: continuous from {stamp(history_from)}, {len(months)} months")
    return 0


if __name__ == "__main__":
    sys.exit(main())
