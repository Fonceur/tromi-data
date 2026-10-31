#!/usr/bin/env python3
"""Record every Régie gas-price change, because nobody else keeps them.

The Régie republishes its feed every 5 minutes and keeps only the latest files,
so a price not recorded on the day cannot be recovered later. Each run compares
the feed with `state/latest.json` and appends what changed to
`history/YYYY/MM/DD.ndjson` (UTC day of the feed's `generated_at`):

    {"t":"2026-10-31T02:00:00Z","id":"<station id>","f":"REGULIER","c":1994}

`c` is the price in tenths of a cent (1994 = 199.4¢). `c: null` means the grade
stopped being sold, or the station left the feed.

The parsing rules mirror the app's `RegieEssenceRemoteDataSource`, so station ids
and grades join directly onto its `price_snapshots` table. Keep the two in step.
"""
import gzip
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FEED_URL = "https://regieessencequebec.ca/stations.geojson.gz"
USER_AGENT = "tromi-data/1.0 (https://play.google.com/store/apps/details?id=ca.romi.tromi)"
ROOT = Path(__file__).resolve().parent

OPERATING_STATUS = "en opération"
FUELS = {"Régulier": "REGULIER", "Super": "SUPER", "Diesel": "DIESEL"}

# Québec with margin; same box as the app and its live-feed contract test.
LAT_RANGE = (44.5, 63.0)
LON_RANGE = (-80.0, -56.5)

# Above these shares the feed changed shape, not the province: keep everything,
# as the app does, rather than lose a day of history that can't be re-fetched.
MAX_INACTIVE_SHARE = 0.5
MAX_MISPLACED_SHARE = 0.5

# Below these a bad payload must not reach the archive: write nothing and fail.
MIN_STATIONS = 2000
MIN_PRICED_SHARE = 0.5


class FeedRejected(Exception):
    """The feed looks wrong; record nothing and fail the run so someone is told."""


def warn(message):
    print(f"::warning::{message}")


def station_id(address, postal_code):
    """Same steps, in the same order, as `FeatureDto.toDomain`."""
    return f"{address.strip()}_{postal_code.strip()}".lower().replace(" ", "_")


def parse_price(text):
    """'199.4¢' -> 1994 (tenths of a cent). None when absent or non-numeric."""
    if text is None:
        return None
    try:
        cents = float(text.rstrip("¢"))
    except ValueError:
        return None
    return round(cents * 10) if math.isfinite(cents) else None


def parse_feature(feature):
    """One feed feature as a station dict; raises on a degenerate record."""
    lon, lat = feature["geometry"]["coordinates"][:2]
    props = feature["properties"]
    prices = {}
    for price in props.get("Prices") or []:
        fuel = FUELS.get(price.get("GasType") or "")
        cents = parse_price(price.get("Price"))
        # The app snapshots only available, parsed prices (`captureSnapshots`).
        if fuel and price.get("IsAvailable") and cents is not None:
            prices[fuel] = cents
    return {
        "id": station_id(props.get("Address") or "", props.get("PostalCode") or ""),
        "name": props.get("Name") or "",
        "brand": props.get("brand") or "",
        "region": props.get("Region") or "",
        "status": (props.get("Status") or "").strip(),
        "lat": lat,
        "lon": lon,
        "prices": prices,
    }


def parse_stations(feed):
    stations = []
    for feature in feed.get("features") or []:
        try:
            stations.append(parse_feature(feature))
        except (KeyError, TypeError, ValueError, IndexError) as e:
            warn(f"Skipping malformed feature: {e!r}")
    return stations


def is_operating(station):
    return not station["status"] or station["status"].lower() == OPERATING_STATUS


def in_quebec(station):
    return (LAT_RANGE[0] <= station["lat"] <= LAT_RANGE[1]
            and LON_RANGE[0] <= station["lon"] <= LON_RANGE[1])


def keep_unless_most_fail(stations, predicate, max_share, label):
    """The app's `partitionBy*`: drop failures, unless that would drop most of the feed."""
    kept = [s for s in stations if predicate(s)]
    dropped = len(stations) - len(kept)
    if dropped > len(stations) * max_share:
        warn(f"{dropped} of {len(stations)} stations {label}: feed changed? Keeping every station.")
        return stations
    return kept


def select_stations(stations):
    stations = keep_unless_most_fail(stations, is_operating, MAX_INACTIVE_SHARE, "not operating")
    return keep_unless_most_fail(stations, in_quebec, MAX_MISPLACED_SHARE, "outside Québec")


def check_feed(stations):
    if len(stations) < MIN_STATIONS:
        raise FeedRejected(f"only {len(stations)} stations (floor {MIN_STATIONS})")
    priced = sum(1 for s in stations if s["prices"])
    if priced < len(stations) * MIN_PRICED_SHARE:
        raise FeedRejected(f"only {priced} of {len(stations)} stations have a price")


def price_map(stations):
    """{'<id>|<FUEL>': cents}. Two features can share a derived id; the later one wins."""
    return {f"{s['id']}|{fuel}": c for s in stations for fuel, c in s["prices"].items()}


def diff_prices(previous, current):
    """Sorted [(key, cents or None)] of what moved between two price maps."""
    changed = [(k, c) for k, c in current.items() if previous.get(k) != c]
    gone = [(k, None) for k in previous if k not in current]
    return sorted(changed + gone)


def feed_time(feed):
    """'2026-10-31T02:00:00.3574647Z' -> '2026-10-31T02:00:00Z'."""
    generated = (feed.get("metadata") or {}).get("generated_at") or ""
    if len(generated) < 19:
        raise FeedRejected(f"bad generated_at: {generated!r}")
    return generated[:19] + "Z"


def history_path(root, stamp):
    return root / "history" / stamp[:4] / stamp[5:7] / f"{stamp[8:10]}.ndjson"


def change_line(stamp, key, cents):
    station, fuel = key.rsplit("|", 1)
    return json.dumps({"t": stamp, "id": station, "f": fuel, "c": cents},
                      ensure_ascii=False, separators=(",", ":"))


def write_lines(path, mapping):
    """One sorted entry per line, so a commit touches only the lines that moved."""
    lines = [f"{json.dumps(k, ensure_ascii=False)}:{json.dumps(v, ensure_ascii=False, sort_keys=True)}"
             for k, v in sorted(mapping.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def record(feed, etag, root):
    """Apply one decoded feed to the archive. Returns the number of changes written."""
    stamp = feed_time(feed)
    stations = select_stations(parse_stations(feed))
    check_feed(stations)

    state = root / "state"
    last = load_json(state / "feed.json")
    if last.get("generated_at", "") > stamp:
        print(f"Feed {stamp} is older than {last['generated_at']}: stale cache, skipping.")
        return 0

    changes = diff_prices(load_json(state / "latest.json"), price_map(stations))
    if changes:
        path = history_path(root, stamp)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as out:
            out.writelines(change_line(stamp, k, c) + "\n" for k, c in changes)

    write_lines(state / "latest.json", price_map(stations))
    write_lines(state / "stations.json", {
        s["id"]: {k: s[k] for k in ("name", "brand", "region", "lat", "lon")} for s in stations
    })
    write_lines(state / "feed.json", {"etag": etag, "generated_at": stamp})
    return len(changes)


def fetch(etag, attempts=3):
    """(body, etag), or (None, etag) on 304. Retries a transient failure."""
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(FEED_URL, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read(), response.headers.get("ETag")
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return None, etag
            if attempt == attempts:
                raise
        except OSError:
            if attempt == attempts:
                raise
        time.sleep(30 * attempt)


def decode(body):
    return json.loads(gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body)


def main():
    etag = load_json(ROOT / "state" / "feed.json").get("etag")
    body, new_etag = fetch(etag)
    if body is None:
        print("Feed unchanged (304).")
        return 0
    try:
        count = record(decode(body), new_etag, ROOT)
    except FeedRejected as e:
        print(f"::error::Feed rejected, nothing recorded: {e}")
        return 1
    print(f"Recorded {count} change(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
