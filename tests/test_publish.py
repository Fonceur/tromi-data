import gzip
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import publish  # noqa: E402

NOW = "2026-11-30T12:00:00Z"
DAY = 24 * 3600


def row(t, cents, sid="a", fuel="REGULIER", order=0):
    return (t, order, sid, fuel, cents)


class Build(unittest.TestCase):
    def test_keeps_only_changes_as_published(self):
        rows = [row("2026-11-20T00:00:00Z", 1994), row("2026-11-20T03:00:00Z", 1994),
                row("2026-11-21T00:00:00Z", 1899)]
        _, _, stations = publish.build(rows, NOW)
        self.assertEqual(stations["a"]["REGULIER"], [
            [publish.epoch("2026-11-20T00:00:00Z"), 1994],
            [publish.epoch("2026-11-21T00:00:00Z"), 1899],
        ])

    def test_the_window_is_the_30_days_up_to_now(self):
        start, end, _ = publish.build([], NOW)
        self.assertEqual(end - start, 30 * DAY)
        self.assertEqual(end, publish.epoch(NOW))

    def test_a_price_older_than_the_window_only_suppresses_a_repeat(self):
        rows = [row("2026-10-01T00:00:00Z", 1994),   # before the window
                row("2026-11-10T00:00:00Z", 1994),   # same price: not a change
                row("2026-11-12T00:00:00Z", 2010)]
        _, _, stations = publish.build(rows, NOW)
        self.assertEqual(stations["a"]["REGULIER"], [[publish.epoch("2026-11-12T00:00:00Z"), 2010]])

    def test_a_pair_that_never_changed_in_the_window_is_absent(self):
        _, _, stations = publish.build([row("2026-10-01T00:00:00Z", 1994)], NOW)
        self.assertEqual(stations, {})

    def test_null_is_skipped_and_a_return_to_the_same_price_is_no_change(self):
        rows = [row("2026-11-20T00:00:00Z", 1994), row("2026-11-21T00:00:00Z", None),
                row("2026-11-22T00:00:00Z", 1994)]
        _, _, stations = publish.build(rows, NOW)
        self.assertEqual(stations["a"]["REGULIER"], [[publish.epoch("2026-11-20T00:00:00Z"), 1994]])

    def test_rows_after_now_are_left_for_the_next_build(self):
        rows = [row("2026-11-20T00:00:00Z", 1994), row("2026-12-01T00:00:00Z", 2010)]
        _, _, stations = publish.build(rows, NOW)
        self.assertEqual(len(stations["a"]["REGULIER"]), 1)

    def test_grades_and_stations_stay_apart(self):
        rows = [row("2026-11-20T00:00:00Z", 1994), row("2026-11-20T00:00:00Z", 1994, fuel="SUPER"),
                row("2026-11-20T00:00:00Z", 1994, sid="b")]
        _, _, stations = publish.build(rows, NOW)
        self.assertEqual(set(stations), {"a", "b"})
        self.assertEqual(set(stations["a"]), {"REGULIER", "SUPER"})


class LoadRows(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "history" / "seed").mkdir(parents=True)
        (self.root / "history" / "2026" / "10").mkdir(parents=True)

    def tearDown(self):
        self.dir.cleanup()

    def write_seed(self, name, rows):
        with gzip.open(self.root / "history" / "seed" / name, "wt", encoding="utf-8") as out:
            out.writelines(json.dumps(r) + "\n" for r in rows)

    def test_seed_rows_only_fill_the_time_before_collection(self):
        self.write_seed("phone.ndjson.gz", [
            {"t": "2026-10-30T00:00:00Z", "id": "a", "f": "REGULIER", "c": 1990},
            {"t": "2026-10-31T03:00:00Z", "id": "a", "f": "REGULIER", "c": 1111},
        ])
        (self.root / "history" / "2026" / "10" / "31.ndjson").write_text(
            json.dumps({"t": "2026-10-31T02:30:00Z", "id": "a", "f": "REGULIER", "c": 1994}) + "\n",
            encoding="utf-8")
        rows = publish.load_rows(self.root)
        self.assertEqual([(r[0], r[4]) for r in rows],
                         [("2026-10-30T00:00:00Z", 1990), ("2026-10-31T02:30:00Z", 1994)])

    def test_rows_come_out_oldest_first_across_files(self):
        self.write_seed("b.ndjson.gz", [{"t": "2026-10-02T00:00:00Z", "id": "a", "f": "SUPER", "c": 2}])
        self.write_seed("a.ndjson.gz", [{"t": "2026-10-03T00:00:00Z", "id": "a", "f": "SUPER", "c": 3},
                                        {"t": "2026-10-01T00:00:00Z", "id": "a", "f": "SUPER", "c": 1}])
        self.assertEqual([r[4] for r in publish.load_rows(self.root)], [1, 2, 3])

    def test_since_skips_older_daily_files_and_the_seed(self):
        self.write_seed("phone.ndjson.gz", [{"t": "2026-10-30T00:00:00Z", "id": "a", "f": "SUPER", "c": 1}])
        (self.root / "history" / "2026" / "11").mkdir()
        for day, cents in (("10/31", 2), ("11/05", 3), ("11/06", 4)):
            (self.root / "history" / "2026" / f"{day}.ndjson").write_text(json.dumps(
                {"t": f"2026-{day.replace('/', '-')}T12:00:00Z", "id": "a", "f": "SUPER", "c": cents}) + "\n",
                encoding="utf-8")
        self.assertEqual([r[4] for r in publish.load_rows(self.root, since="2026-11-05T00:00:00Z")], [3, 4])
        self.assertEqual([r[4] for r in publish.load_rows(self.root)], [1, 2, 3, 4])


class Document(unittest.TestCase):
    def test_every_file_carries_the_source_and_the_terms(self):
        doc = publish.document(0, 1, {})
        self.assertEqual(doc["source"], "Régie essence Québec, Régie de l'énergie")
        self.assertEqual(doc["schema"], 1)
        for words in ("not modified", "visible at all times", "mandated", "independent"):
            self.assertIn(words, doc["terms"])

    def test_the_regional_file_says_the_averages_are_tromis_not_the_regies(self):
        doc = publish.regional_document(date(2026, 11, 1), date(2026, 11, 30), 0, {})
        self.assertEqual(doc["source"], "Régie essence Québec, Régie de l'énergie")
        self.assertEqual(doc["schema"], 1)
        self.assertEqual((doc["from"], doc["to"]), ("2026-11-01", "2026-11-30"))
        for words in ("calculated by Tromi", "not the Régie's", "averages of its own",
                      "credited and visible", "independent", "mandated"):
            self.assertIn(words, doc["terms"])

    def test_the_two_files_number_their_formats_apart(self):
        # The app rejects a backfill file whose schema isn't the one it reads, so a regional
        # format change must never move it.
        publish.REGIONAL_SCHEMA, saved = 2, publish.REGIONAL_SCHEMA
        try:
            self.assertEqual(publish.document(0, 1, {})["schema"], 1)
            self.assertEqual(publish.regional_document(
                date(2026, 11, 1), date(2026, 11, 30), 0, {})["schema"], 2)
        finally:
            publish.REGIONAL_SCHEMA = saved


# Five stations per region, so a region clears MIN_STATIONS_PER_POINT.
MTL = {f"m{i}": "Montréal" for i in range(5)}
# The collector's first run, a full snapshot of the feed, as every baseline row is stamped.
BASE = publish.COLLECTION_START


def five(t, cents, fuel="REGULIER", prefix="m"):
    """The same price at five stations, one per id in MTL (or another prefix)."""
    return [row(t, cents, sid=f"{prefix}{i}", fuel=fuel) for i in range(5)]


def by_day(regions, region="Montréal", fuel="REGULIER"):
    return {day: (avg, n) for day, avg, n in regions[region][fuel]}


class Regional(unittest.TestCase):
    # 2026-12-01 07:00 EST: the last complete Québec day is Nov 30.
    NOW = "2026-12-01T12:00:00Z"

    def test_the_window_is_the_30_complete_quebec_days_before_the_feed(self):
        first, last, _ = publish.build_regional([], self.NOW, {})
        self.assertEqual((first.isoformat(), last.isoformat()), ("2026-11-01", "2026-11-30"))
        # 03:00 UTC on Dec 1 is still Nov 30 in Québec, so Nov 30 isn't complete yet.
        _, last, _ = publish.build_regional([], "2026-12-01T03:00:00Z", {})
        self.assertEqual(last.isoformat(), "2026-11-29")

    def test_a_close_carries_forward_until_the_price_changes(self):
        rows = five(BASE, 1800) + five("2026-11-15T12:00:00Z", 1900)
        days = by_day(publish.build_regional(rows, self.NOW, MTL)[2])
        self.assertEqual(len(days), 30)
        self.assertEqual(days["2026-11-01"], (1800, 5))
        self.assertEqual(days["2026-11-14"], (1800, 5))
        self.assertEqual(days["2026-11-15"], (1900, 5))
        self.assertEqual(days["2026-11-30"], (1900, 5))

    def test_the_day_ends_at_quebec_midnight_across_the_dst_change(self):
        # DST ended 2026-11-01 at 06:00 UTC. 04:30 UTC on Oct 31 is 00:30 EDT, so Oct 31
        # (a fixed UTC-5 would say Oct 30). 04:30 UTC on Nov 2 is Nov 1, 23:30 EST, so Nov 1
        # (UTC days or a fixed UTC-4 would say Nov 2). 05:00 UTC on Nov 3 is midnight EST.
        rows = (five(BASE, 1800) + five("2026-10-31T04:30:00Z", 1810)
                + five("2026-11-02T04:30:00Z", 1820) + five("2026-11-03T05:00:00Z", 1830))
        days = by_day(publish.build_regional(rows, "2026-11-10T12:00:00Z", MTL)[2])
        self.assertEqual(days["2026-10-30"], (1800, 5))
        self.assertEqual(days["2026-10-31"], (1810, 5))
        self.assertEqual(days["2026-11-01"], (1820, 5))
        self.assertEqual(days["2026-11-02"], (1820, 5))
        self.assertEqual(days["2026-11-03"], (1830, 5))

    def test_the_average_rounds_half_up_to_a_tenth_of_a_cent(self):
        rows = [row(BASE, c, sid=f"m{i}") for i, c in enumerate(
            (1994, 1995, 1994, 1995, 1994))] + [row(BASE, 1995, sid="m5")]
        regions = dict(MTL, m5="Montréal")
        self.assertEqual(by_day(publish.build_regional(rows, self.NOW, regions)[2])["2026-11-30"],
                         (1995, 6))  # 1994.5 -> 1995, where round() would give 1994

    def test_a_null_takes_the_station_out_from_that_day_on(self):
        rows = (five(BASE, 1800) + [row(BASE, 2300, sid="m5")]
                + [row("2026-11-20T12:00:00Z", None, sid="m5")])
        days = by_day(publish.build_regional(rows, self.NOW, dict(MTL, m5="Montréal"))[2])
        self.assertEqual(days["2026-11-19"], (1883, 6))
        self.assertEqual(days["2026-11-20"], (1800, 5))

    def test_a_point_under_five_stations_is_left_out(self):
        rows = five(BASE, 1800)[:4]
        self.assertEqual(publish.build_regional(rows, self.NOW, MTL)[2], {})

    def test_a_day_that_knows_under_80_percent_of_the_last_days_stations_is_left_out(self):
        regions = {f"m{i}": "Montréal" for i in range(10)}
        early = [row(BASE, 1800, sid=f"m{i}") for i in range(7)]
        late = [row("2026-11-10T12:00:00Z", 1800, sid=f"m{i}") for i in range(7, 10)]
        days = by_day(publish.build_regional(early + late, self.NOW, regions)[2])
        self.assertNotIn("2026-11-09", days)  # 7 of 10
        self.assertEqual(days["2026-11-10"], (1800, 10))
        self.assertEqual(len(days), 21)

    def test_a_station_with_no_region_in_the_current_feed_counts_nowhere(self):
        rows = five(BASE, 1800) + [row(BASE, 2500, sid="gone")]
        self.assertEqual(by_day(publish.build_regional(rows, self.NOW, MTL)[2])["2026-11-30"],
                         (1800, 5))

    def test_regions_and_grades_stay_apart(self):
        regions = dict(MTL, **{f"q{i}": "Capitale-Nationale" for i in range(5)})
        rows = (five(BASE, 1800) + five(BASE, 2000, fuel="DIESEL")
                + five(BASE, 1700, prefix="q"))
        out = publish.build_regional(sorted(rows), self.NOW, regions)[2]
        self.assertEqual(set(out), {"Montréal", "Capitale-Nationale"})
        self.assertEqual(set(out["Montréal"]), {"REGULIER", "DIESEL"})
        self.assertEqual(by_day(out, "Capitale-Nationale")["2026-11-30"], (1700, 5))
        self.assertEqual(by_day(out, fuel="DIESEL")["2026-11-30"], (2000, 5))

    def test_rows_after_the_last_complete_day_are_left_for_the_next_build(self):
        rows = five(BASE, 1800) + five("2026-12-01T06:00:00Z", 1900)
        days = by_day(publish.build_regional(rows, self.NOW, MTL)[2])
        self.assertEqual(days["2026-11-30"], (1800, 5))

    def test_a_price_only_the_seed_knew_is_dropped_when_collection_begins(self):
        # m5's diesel left the feed before the collector's first run, which therefore wrote
        # no null for it. That run began at 22:30 EDT on Oct 30, so Oct 30 is its first day.
        rows = (five("2026-10-20T12:00:00Z", 1800) + [row("2026-10-20T12:00:00Z", 2300, sid="m5")]
                + five(BASE, 1800))
        days = by_day(publish.build_regional(rows, "2026-11-10T12:00:00Z", dict(MTL, m5="Montréal"))[2])
        self.assertEqual(days["2026-10-29"], (1883, 6))
        self.assertEqual(days["2026-10-30"], (1800, 5))

    def test_reading_never_starts_after_the_collectors_full_snapshot(self):
        # A price unchanged since that run is known only from it, however old it gets.
        self.assertEqual(publish.regional_since("2027-06-01T12:00:00Z"), BASE)
        # Before then, a month ahead of the window's first day (Oct 9, EDT), as the backfill.
        self.assertEqual(publish.regional_since("2026-11-08T16:00:00Z"), "2026-09-09T04:00:00Z")

    def test_too_few_regional_lines_blocks_publishing(self):
        regions = {f"r{i}": {"REGULIER": [["2026-11-30", 1800, 5]]} for i in range(publish.MIN_REGIONS)}
        self.assertIsNone(publish.regional_problem(regions))
        del regions["r0"]
        self.assertIn("regular-grade", publish.regional_problem(regions))


class History(unittest.TestCase):
    """The month files and their index (W171): Tromi for iOS draws any days since the
    archive's continuous start from them."""

    def test_continuity_starts_after_the_last_gap_of_more_than_a_day(self):
        rows = [row("2026-09-12T12:00:00Z", 1), row("2026-09-30T00:00:00Z", 2),
                row("2026-09-30T23:00:00Z", 3), row("2026-10-01T23:00:00Z", 4)]
        end = publish.epoch("2026-10-02T00:00:00Z")
        # 18 days between the first two; then 23 h and exactly 24 h, both kept.
        self.assertEqual(publish.continuous_from(rows, end), publish.epoch("2026-09-30T00:00:00Z"))
        rows.append(row("2026-10-03T00:00:01Z", 5))
        self.assertEqual(publish.continuous_from(rows, publish.epoch("2026-10-04T00:00:00Z")),
                         publish.epoch("2026-10-03T00:00:01Z"))

    def test_a_refresh_after_end_is_left_for_the_next_build(self):
        rows = [row("2026-10-01T00:00:00Z", 1), row("2026-10-05T00:00:00Z", 2)]
        self.assertEqual(publish.continuous_from(rows, publish.epoch("2026-10-02T00:00:00Z")),
                         publish.epoch("2026-10-01T00:00:00Z"))

    def test_months_are_quebec_months_held_to_the_archive(self):
        start = publish.epoch("2026-10-01T02:15:58Z")   # Sept 30, 22:15 in Québec
        end = publish.epoch("2027-01-10T00:00:00Z")
        months = publish.history_months(start, end)
        self.assertEqual([m[0] for m in months], ["2026-09", "2026-10", "2026-11", "2026-12", "2027-01"])
        self.assertEqual(months[0][1], start)
        self.assertEqual(months[1][1], publish.epoch("2026-10-01T04:00:00Z"))   # EDT midnight
        self.assertEqual(months[2][1], publish.epoch("2026-11-01T04:00:00Z"))   # still EDT
        self.assertEqual(months[3][1], publish.epoch("2026-12-01T05:00:00Z"))   # EST midnight
        self.assertEqual(months[-1][2], end)
        self.assertEqual(months[0][2], months[1][1])

    def test_each_month_holds_its_changes_and_the_prices_in_force_at_its_start(self):
        start = publish.epoch("2026-11-05T00:00:00Z")
        rows = [row("2026-11-10T00:00:00Z", 1994), row("2026-11-20T00:00:00Z", 1899),
                row("2026-11-25T00:00:00Z", 1899),                       # a repeat
                row("2026-12-03T00:00:00Z", 1950), row("2026-11-12T00:00:00Z", 2100, sid="b")]
        rows.sort(key=lambda r: r[0])
        months = publish.build_months(rows, start, publish.epoch("2026-12-10T00:00:00Z"))
        _, _, starts, stations = months["2026-11"]
        self.assertEqual(starts, {})
        self.assertEqual(stations["a"]["REGULIER"], [[publish.epoch("2026-11-10T00:00:00Z"), 1994],
                                                     [publish.epoch("2026-11-20T00:00:00Z"), 1899]])
        lo, _, starts, stations = months["2026-12"]
        self.assertEqual(lo, publish.epoch("2026-12-01T05:00:00Z"))
        self.assertEqual(starts, {"a": {"REGULIER": 1899}, "b": {"REGULIER": 2100}})
        self.assertEqual(stations, {"a": {"REGULIER": [[publish.epoch("2026-12-03T00:00:00Z"), 1950]]}})

    def test_a_month_with_no_change_still_carries_its_starting_prices(self):
        rows = [row("2026-11-10T00:00:00Z", 1994)]
        months = publish.build_months(rows, publish.epoch("2026-11-05T00:00:00Z"),
                                      publish.epoch("2027-01-10T00:00:00Z"))
        self.assertEqual(months["2026-12"][2], {"a": {"REGULIER": 1994}})
        self.assertEqual(months["2027-01"][2], {"a": {"REGULIER": 1994}})
        self.assertEqual(months["2027-01"][3], {})

    def test_a_null_takes_a_pair_out_of_the_next_starts(self):
        rows = [row("2026-11-10T00:00:00Z", 1994), row("2026-11-11T00:00:00Z", None)]
        months = publish.build_months(rows, publish.epoch("2026-11-05T00:00:00Z"),
                                      publish.epoch("2026-12-10T00:00:00Z"))
        self.assertEqual(months["2026-12"][2], {})
        self.assertEqual(months["2026-11"][3]["a"]["REGULIER"], [[publish.epoch("2026-11-10T00:00:00Z"), 1994]])

    def test_a_pair_back_at_its_old_price_after_a_null_is_a_row_in_its_month(self):
        # Out of the feed at December's start, back at its old price on the 3rd: with no
        # start and no row, December alone couldn't draw it.
        rows = [row("2026-11-10T00:00:00Z", 1994), row("2026-11-20T00:00:00Z", None),
                row("2026-12-03T00:00:00Z", 1994)]
        months = publish.build_months(rows, publish.epoch("2026-11-05T00:00:00Z"),
                                      publish.epoch("2026-12-10T00:00:00Z"))
        self.assertEqual(months["2026-12"][2], {})
        self.assertEqual(months["2026-12"][3]["a"]["REGULIER"], [[publish.epoch("2026-12-03T00:00:00Z"), 1994]])
        # Within one month, as in the 30-day file, a return to the same price is no change.
        self.assertEqual(len(months["2026-11"][3]["a"]["REGULIER"]), 1)

    def test_rows_before_the_continuous_start_are_left_out(self):
        rows = [row("2026-11-01T00:00:00Z", 1500), row("2026-11-10T00:00:00Z", 1994)]
        months = publish.build_months(rows, publish.epoch("2026-11-05T00:00:00Z"),
                                      publish.epoch("2026-12-10T00:00:00Z"))
        self.assertEqual(months["2026-11"][2], {})
        self.assertEqual(months["2026-11"][3]["a"]["REGULIER"], [[publish.epoch("2026-11-10T00:00:00Z"), 1994]])

    def test_a_price_only_the_seed_knew_is_forgotten_when_collection_begins(self):
        # m's diesel left the feed before the collector's first run, which wrote no null for it.
        rows = [row("2026-10-20T12:00:00Z", 2300, sid="m", fuel="DIESEL"),
                row("2026-10-20T12:00:00Z", 1800), row(publish.COLLECTION_START, 1800)]
        months = publish.build_months(rows, publish.epoch("2026-10-15T00:00:00Z"),
                                      publish.epoch("2026-11-10T00:00:00Z"))
        self.assertEqual(months["2026-11"][2], {"a": {"REGULIER": 1800}})
        # The collector's baseline is written again, as its first reading.
        self.assertEqual(len(months["2026-10"][3]["a"]["REGULIER"]), 2)

    def test_the_files_carry_the_source_the_terms_and_their_own_format_number(self):
        doc = publish.month_document("2026-11", 1, 2, {}, {})
        index = publish.history_index(1, 2, ["2026-11"])
        for d in (doc, index):
            self.assertEqual(d["source"], "Régie essence Québec, Régie de l'énergie")
            self.assertIn("not modified", d["terms"])
            self.assertEqual(d["schema"], publish.HISTORY_SCHEMA)
        self.assertEqual(index["months"], ["2026-11"])
        self.assertEqual((index["from"], index["to"]), (1, 2))

    def test_a_collected_month_with_too_few_stations_blocks_publishing(self):
        start = publish.epoch(publish.COLLECTION_START)
        full = {f"s{i}": {"REGULIER": 1994} for i in range(publish.MIN_STATIONS)}
        self.assertIsNone(publish.history_problem({"2026-11": (start + 1, start + 2, full, {})}))
        del full["s0"]
        self.assertIn("2026-11", publish.history_problem({"2026-11": (start + 1, start + 2, full, {})}))
        # A seed month knows what the phones knew, so it's not held to the floor.
        self.assertIsNone(publish.history_problem({"2026-10": (start - 10, start - 5, {}, {})}))


if __name__ == "__main__":
    unittest.main()
