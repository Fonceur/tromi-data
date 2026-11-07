import gzip
import json
import sys
import tempfile
import unittest
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
        for words in ("not modified", "visible at all times", "mandated", "independent"):
            self.assertIn(words, doc["terms"])


if __name__ == "__main__":
    unittest.main()
