import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collect  # noqa: E402

STAMP = "2026-10-31T02:00:00.3574647Z"


def feature(address="1 rue Test, Québec", postal="G1V 1A1", status="En opération",
            lon=-71.2, lat=46.8, prices=None):
    if prices is None:
        prices = [{"GasType": "Régulier", "Price": "199.4¢", "IsAvailable": True}]
    return {
        "geometry": {"coordinates": [lon, lat]},
        "properties": {"Address": address, "PostalCode": postal, "Status": status,
                       "Name": "Station", "brand": "Esso", "Region": "Estrie", "Prices": prices},
    }


def feed(count=2100, stamp=STAMP, **kwargs):
    return {
        "metadata": {"generated_at": stamp},
        "features": [feature(address=f"{i} rue Test", **kwargs) for i in range(count)],
    }


class StationId(unittest.TestCase):
    def test_matches_the_documented_example(self):
        self.assertEqual(
            collect.station_id("9531 boul. Gouin Ouest, Montréal", "H8Y 1R2"),
            "9531_boul._gouin_ouest,_montréal_h8y_1r2",
        )

    def test_trims_before_joining(self):
        self.assertEqual(collect.station_id("  1 Rue A ", " G1V 1A1 "), "1_rue_a_g1v_1a1")

    def test_replaces_only_plain_spaces(self):
        self.assertEqual(collect.station_id("1 Rue", "G1V"), "1 rue_g1v")


class ParsePrice(unittest.TestCase):
    def test_strips_the_cent_sign(self):
        self.assertEqual(collect.parse_price("199.4¢"), 1994)

    def test_rejects_blank_null_and_text(self):
        for text in ("", "¢", None, "n/a¢", "nan¢", "inf¢"):
            self.assertIsNone(collect.parse_price(text), text)


class ParseFeature(unittest.TestCase):
    def test_maps_every_grade(self):
        prices = [{"GasType": g, "Price": "200.0¢", "IsAvailable": True}
                  for g in ("Régulier", "Super", "Diesel")]
        self.assertEqual(set(collect.parse_feature(feature(prices=prices))["prices"]),
                         {"REGULIER", "SUPER", "DIESEL"})

    def test_drops_unknown_grades_unavailable_and_unparsed_prices(self):
        prices = [
            {"GasType": "Propane", "Price": "100.0¢", "IsAvailable": True},
            {"GasType": "Super", "Price": "210.0¢", "IsAvailable": False},
            {"GasType": "Diesel", "Price": None, "IsAvailable": True},
        ]
        self.assertEqual(collect.parse_feature(feature(prices=prices))["prices"], {})

    def test_a_degenerate_feature_is_skipped_not_fatal(self):
        bad = {"geometry": {"coordinates": []}, "properties": {}}
        self.assertEqual(len(collect.parse_stations({"features": [bad, feature()]})), 1)


class SelectStations(unittest.TestCase):
    def stations(self, **kwargs):
        return collect.parse_stations(feed(**kwargs))

    def test_blank_status_counts_as_operating_and_case_is_ignored(self):
        for status in ("", "  ", "En opération", " EN OPÉRATION "):
            self.assertTrue(collect.is_operating(collect.parse_feature(feature(status=status))), status)

    def test_a_closed_station_is_left_out(self):
        stations = self.stations(count=10)
        stations[0]["status"] = "Fermé"
        self.assertEqual(len(collect.select_stations(stations)), 9)

    def test_keeps_everything_when_most_of_the_feed_reads_closed(self):
        self.assertEqual(len(collect.select_stations(self.stations(count=10, status="Ouvert"))), 10)

    def test_null_island_is_left_out(self):
        stations = self.stations(count=10)
        stations[0]["lat"] = stations[0]["lon"] = 0.0
        self.assertEqual(len(collect.select_stations(stations)), 9)

    def test_the_feeds_real_edges_are_kept(self):
        self.assertTrue(collect.in_quebec(collect.parse_feature(feature(lat=62.42, lon=-79.50))))
        self.assertTrue(collect.in_quebec(collect.parse_feature(feature(lat=45.01, lon=-61.54))))

    def test_keeps_everything_when_most_of_the_feed_lands_outside_quebec(self):
        self.assertEqual(len(collect.select_stations(self.stations(count=10, lat=0.0, lon=0.0))), 10)


class Diff(unittest.TestCase):
    def test_reports_new_changed_and_gone_but_not_unchanged(self):
        previous = {"a|REGULIER": 1000, "b|REGULIER": 1100, "c|REGULIER": 1200}
        current = {"a|REGULIER": 1000, "b|REGULIER": 1150, "d|REGULIER": 1300}
        self.assertEqual(collect.diff_prices(previous, current), [
            ("b|REGULIER", 1150), ("c|REGULIER", None), ("d|REGULIER", 1300),
        ])

    def test_a_shared_derived_id_resolves_to_the_later_feature(self):
        stations = [{"id": "x", "prices": {"SUPER": 1}}, {"id": "x", "prices": {"SUPER": 2}}]
        self.assertEqual(collect.price_map(stations), {"x|SUPER": 2})


class Record(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.day = self.root / "history" / "2026" / "10" / "31.ndjson"

    def lines(self):
        return [json.loads(line) for line in self.day.read_text(encoding="utf-8").splitlines()]

    def test_the_first_run_records_a_baseline(self):
        self.assertEqual(collect.record(feed(), '"e1"', self.root), 2100)
        self.assertEqual(self.lines()[0], {
            "t": "2026-10-31T02:00:00Z", "id": "0_rue_test_g1v_1a1", "f": "REGULIER", "c": 1994,
        })

    def test_an_unchanged_feed_writes_nothing(self):
        collect.record(feed(), '"e1"', self.root)
        self.assertEqual(collect.record(feed(stamp="2026-10-31T02:15:00.1Z"), '"e2"', self.root), 0)
        self.assertEqual(len(self.lines()), 2100)

    def test_only_the_change_is_appended(self):
        collect.record(feed(), '"e1"', self.root)
        later = feed(stamp="2026-10-31T02:15:00.1Z")
        later["features"][0]["properties"]["Prices"][0]["Price"] = "189.9¢"
        self.assertEqual(collect.record(later, '"e2"', self.root), 1)
        self.assertEqual(self.lines()[-1]["c"], 1899)

    def test_a_grade_that_stops_being_sold_is_recorded_as_null(self):
        collect.record(feed(), '"e1"', self.root)
        later = feed(stamp="2026-10-31T02:15:00.1Z")
        later["features"][0]["properties"]["Prices"][0]["IsAvailable"] = False
        collect.record(later, '"e2"', self.root)
        self.assertIsNone(self.lines()[-1]["c"])

    def test_a_stale_cached_feed_is_ignored(self):
        collect.record(feed(), '"e1"', self.root)
        older = feed(stamp="2026-10-31T01:45:00.1Z")
        older["features"][0]["properties"]["Prices"][0]["Price"] = "1.0¢"
        self.assertEqual(collect.record(older, '"e0"', self.root), 0)

    def test_a_thin_feed_is_rejected_and_touches_nothing(self):
        with self.assertRaises(collect.FeedRejected):
            collect.record(feed(count=100), '"e1"', self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_mostly_unpriced_feed_is_rejected(self):
        with self.assertRaises(collect.FeedRejected):
            collect.record(feed(prices=[]), '"e1"', self.root)

    def test_a_feed_without_a_timestamp_is_rejected(self):
        bad = feed()
        bad["metadata"] = {}
        with self.assertRaises(collect.FeedRejected):
            collect.record(bad, '"e1"', self.root)

    def test_state_diffs_stay_line_based(self):
        collect.record(feed(count=2100), '"e1"', self.root)
        text = (self.root / "state" / "latest.json").read_text(encoding="utf-8")
        self.assertEqual(len(text.splitlines()), 2102)
        self.assertEqual(json.loads(text)["0_rue_test_g1v_1a1|REGULIER"], 1994)


class Decode(unittest.TestCase):
    def test_handles_gzipped_and_plain_bodies(self):
        import gzip
        raw = b'{"a":1}'
        self.assertEqual(collect.decode(gzip.compress(raw)), {"a": 1})
        self.assertEqual(collect.decode(raw), {"a": 1})


if __name__ == "__main__":
    unittest.main()
