import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import make_paste_lists as mpl


def _cfg(allowed_club=None):
    return {"_meta": {"generated_at": "2026-10-02T12:00:00"},
            "player": {"keyword_rules": [
                {"canonical_value": "De Jong", "safe_keywords": ["de jong"]},
                {"canonical_value": "Kessler", "safe_keywords": ["kessler"]},
                {"canonical_value": "Dalot", "safe_keywords": ["dalot"]}],
                "allowed_values": ["De Jong", "Kessler", "Dalot"]},
            "club": {"keyword_rules": [
                {"canonical_value": "Everton FC", "safe_keywords": ["everton"]}],
                "allowed_values": (allowed_club or
                                   [f"Club {i}" for i in range(99)] + ["Everton FC"])}}


def _p(n, title, pf=""):
    return {"handle": f"h{n}", "title": title, "status": "ACTIVE",
            "player_filter": pf, "club_filter": ""}


PRODUCTS = [_p(1, "Harry Maguire Man Utd Jersey"), _p(2, "Maguire Keyring"),
            _p(3, "Upamecano Bayern Home"),
            _p(5, "x", pf="Harry Maguire"), _p(6, "y", pf="No Player"),
            _p(7, "z", pf="Diogo Dalot"),
            {"handle": "h8", "title": "inactive maguire", "status": "DRAFT",
             "player_filter": "", "club_filter": ""}]

GOOD_DELTA = [{"canonical_value": "Maguire", "safe_keywords": ["maguire", "harry maguire"]},
              {"canonical_value": "Upamecano", "safe_keywords": ["upamecano"]},
              {"canonical_value": "Kessler", "add_keywords": ["timo kessler"]}]


class BuildPatchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.prev = os.getcwd()
        os.chdir(self.dir)
        os.makedirs("task-exports")
        os.makedirs("paste")
        with open("task-exports/player.json", "w") as fh:
            fh.write("{}")
        old = os.path.getmtime("task-exports/player.json") - 10 ** 6
        os.utime("task-exports/player.json", (old, old))
        self._write_cfg(_cfg())
        with open("audit-ignore.json", "w") as fh:
            json.dump({"filled_orphans_ok": {"player_filter": ["No Player"]}}, fh)
        with open("products-cache.jsonl", "w") as fh:
            fh.write("")
        self.patches = [
            patch.object(mpl.config, "TASK_CONFIGS", "task-configs.json"),
            patch.object(mpl.config, "AUDIT_IGNORE", "audit-ignore.json"),
            patch.object(mpl.config, "CACHE_PRODUCTS_JSONL", "products-cache.jsonl", create=True),
            patch.object(mpl.config, "paste_dir", lambda: "paste", create=True),
            patch.object(mpl, "load_products", lambda *a, **k: PRODUCTS),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self.prev)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_cfg(self, cfg):
        with open("task-configs.json", "w") as fh:
            json.dump(cfg, fh)

    def _delta(self, rules):
        with open("delta.json", "w") as fh:
            json.dump(rules, fh)
        return "delta.json"

    def _build(self, rules, handle="player", **kw):
        return mpl.build_patch(self._delta(rules), handle, cached=True,
                               log=lambda *a: None, **kw)

    def test_merge_extend_remove_and_roundtrip(self):
        out = self._build(GOOD_DELTA, remove=["De Jong"])
        rules = json.load(open(os.path.join(out, "keyword_rules_json.txt")))
        allowed = json.load(open(os.path.join(out, "allowed_values_json.txt")))
        self.assertEqual({r["canonical_value"] for r in rules},
                         {"Kessler", "Dalot", "Maguire", "Upamecano"})
        dalot = next(r for r in rules if r["canonical_value"] == "Dalot")
        self.assertEqual(dalot, {"canonical_value": "Dalot", "safe_keywords": ["dalot"]})
        kessler = next(r for r in rules if r["canonical_value"] == "Kessler")
        self.assertEqual(kessler["safe_keywords"], ["kessler", "timo kessler"])
        self.assertEqual(allowed, ["Kessler", "Dalot", "Maguire", "Upamecano"])

    def test_extend_dedupes_existing_keyword(self):
        out = self._build([{"canonical_value": "Kessler", "safe_keywords": ["kessler", "timo kessler"]}])
        rules = json.load(open(os.path.join(out, "keyword_rules_json.txt")))
        kessler = next(r for r in rules if r["canonical_value"] == "Kessler")
        self.assertEqual(kessler["safe_keywords"], ["kessler", "timo kessler"])

    def test_refuses_collision_existing(self):
        with self.assertRaises(SystemExit):
            self._build([{"canonical_value": "Frenkie", "safe_keywords": ["de jong"]}])
        self.assertFalse(os.listdir("paste"))

    def test_refuses_duplicate_in_delta(self):
        with self.assertRaises(SystemExit):
            self._build([{"canonical_value": "A", "safe_keywords": ["tah"]},
                         {"canonical_value": "B", "safe_keywords": ["tah"]}])
        self.assertFalse(os.listdir("paste"))

    def test_refuses_stale_export(self):
        now = os.path.getmtime("task-configs.json") + 10 ** 6
        os.utime("task-exports/player.json", (now, now))
        with self.assertRaises(SystemExit):
            self._build(GOOD_DELTA)

    def test_orphan_normalize_csv(self):
        out = self._build(GOOD_DELTA)
        lines = open(os.path.join(out, "orphan-normalize.csv")).read().splitlines()
        self.assertIn("Metafield: custom.player_filter [single_line_text_field]", lines[0])
        body = set(lines[1:])
        self.assertIn('h5,"Harry Maguire","Maguire"', body)
        self.assertIn('h7,"Diogo Dalot","Dalot"', body)
        self.assertEqual(len(body), 2)  # acknowledged "No Player" excluded
        payload = json.load(open(os.path.join(out, "orphan-normalize.json")))
        self.assertIn({"handle": "h5", "expected_current": "Harry Maguire",
                       "set_to": "Maguire"}, payload)

    def test_forecast_counts_in_summary(self):
        out = self._build(GOOD_DELTA)
        summary = open(os.path.join(out, "patch-summary.md")).read()
        self.assertIn("NEW Maguire: 2 blank(s)", summary)
        self.assertIn("NEW Upamecano: 1 blank(s)", summary)

    def test_club_choices_file_and_cap(self):
        out = self._build([{"canonical_value": "Wrexham AFC", "safe_keywords": ["wrexham"]}],
                          handle="club")
        choices = open(os.path.join(out, "definition-choices-to-add.txt")).read().strip()
        self.assertEqual(choices, "Wrexham AFC")
        self._write_cfg(_cfg(allowed_club=[f"Club {i}" for i in range(127)] + ["Everton FC"]))
        with self.assertRaises(SystemExit):
            self._build([{"canonical_value": "Wrexham AFC", "safe_keywords": ["wrexham"]}],
                        handle="club")


if __name__ == "__main__":
    unittest.main()
