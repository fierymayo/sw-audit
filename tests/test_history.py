import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.prev = os.getcwd()
        os.chdir(self.dir)
        os.makedirs("output/summaries")
        self.patches = [
            patch.object(history.config, "OUT_DIR", "output"),
            patch.object(history.config, "HISTORY_DIR", "history"),
            patch.object(history.config, "METRICS_JSONL", "history/metrics.jsonl"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self.prev)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _lines(self):
        with open("history/metrics.jsonl", encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def test_append_products_record_shape(self):
        blanks = {"player_filter": [{"reason": "SYNC_MISS"}, {"reason": "NO_RULE_VALUE"}],
                  "club_filter": []}
        filled = {"rows": {"player_filter": [{"finding": "FILLED_ORPHAN"}]},
                  "orphans": {"player_filter": {"Santos": 1}}}
        summary = {"active": 10, "fill_club_filter": {"pct": 65.0}}
        history.append_products("20260101-0000", [1, 2, 3], summary, blanks, filled, "OK")
        rec = self._lines()[-1]
        self.assertEqual(rec["kind"], "products")
        self.assertEqual(rec["products"], 3)
        self.assertEqual(rec["fill_pct"], {"club_filter": 65.0})
        self.assertEqual(rec["blank_reasons"]["player_filter"],
                         {"SYNC_MISS": 1, "NO_RULE_VALUE": 1})
        self.assertEqual(rec["orphan_products"], {"player_filter": 1})
        self.assertIn("ts", rec)

    def test_append_collections_record_shape(self):
        colls = [{"has_rule": True}, {"has_rule": True}, {"has_rule": False}]
        history.append_collections("20260101-0001", colls, {"AUTOMATABLE": 3}, 9, "OK")
        rec = self._lines()[-1]
        self.assertEqual(rec["kind"], "collections")
        self.assertEqual(rec["collections"], 3)
        self.assertEqual(rec["with_rules"], 2)
        self.assertEqual(rec["candidates"], {"AUTOMATABLE": 3})

    def test_backfill_idempotent_and_ts_from_summary(self):
        with open("output/summaries/fill-rate-summary-20260101-0100.json", "w") as fh:
            json.dump({"generated_at": "2026-01-01T01:00:00+05:00", "active": 5,
                       "fill_club_filter": {"pct": 50.0}}, fh)
        self.assertEqual(history.backfill(log=lambda *a: None), 1)
        self.assertEqual(history.backfill(log=lambda *a: None), 0)
        rec = self._lines()[0]
        self.assertTrue(rec["backfilled"])
        self.assertEqual(rec["ts"], "2026-01-01T01:00:00+05:00")
        self.assertEqual(rec["run"], "20260101-0100")


if __name__ == "__main__":
    unittest.main()
