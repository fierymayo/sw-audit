import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import catalog
import passes
import make_paste_lists as mpl
from matcher import compile_rules


class LoaderRawTests(unittest.TestCase):
    def test_stripped_plus_raw(self):
        rows = [
            {"id": "gid://shopify/Product/1", "handle": "h1", "title": "t", "status": "ACTIVE"},
            {"id": "gid://shopify/Metafield/9", "__parentId": "gid://shopify/Product/1",
             "key": "player_filter", "value": "Minjae "},
            {"id": "gid://shopify/Product/2", "handle": "h2", "title": "t", "status": "ACTIVE"},
            {"id": "gid://shopify/Metafield/8", "__parentId": "gid://shopify/Product/2",
             "key": "player_filter", "value": "   "},
        ]
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        try:
            products = {p["handle"]: p for p in catalog.load_products(path)}
        finally:
            os.remove(path)
        self.assertEqual(products["h1"]["player_filter"], "Minjae")
        self.assertEqual(products["h1"]["_raw"]["player_filter"], "Minjae ")
        self.assertEqual(products["h2"]["player_filter"], "")
        self.assertEqual(products["h2"]["_raw"]["player_filter"], "   ")


def _p(h, raw, title):
    stored = raw.strip()
    return {"id": h, "gid": f"gid://shopify/Product/{h}", "handle": h, "title": title,
            "status": "ACTIVE", "type": "Jerseys", "tags": [],
            "club_filter": "", "country_filter": "", "player_filter": stored,
            "tournament_filter": "",
            "_raw": {"club_filter": "", "country_filter": "", "player_filter": raw,
                     "tournament_filter": ""}}


class FilledWhitespaceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.prev = os.getcwd()
        os.chdir(self.dir)
        self.p = patch.object(passes.config, "AUDIT_IGNORE", "missing-ignore.json")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        os.chdir(self.prev)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_whitespace_classification(self):
        cfg = {"player": {
            "_compiled": compile_rules([
                {"canonical_value": "Rashford", "safe_keywords": ["rashford"]},
                {"canonical_value": "Kim Min-jae", "safe_keywords": ["minjae", "kim min-jae"]}]),
            "_canonicals": {"Rashford", "Kim Min-jae"}}}
        products = [
            _p("h1", "Rashford ", "Rashford Home Jersey"),
            _p("h2", "Minjae ", "Minjae Kim Away Jersey"),
            _p("h3", "Kim Min-jae", "Minjae Kim Home Jersey"),
        ]
        out = passes.pass_filled_check(products, cfg)
        rows = {r["handle"]: r for r in out["rows"]["player_filter"]}
        self.assertEqual(rows["h1"]["finding"], "FILLED_WHITESPACE")
        self.assertEqual(rows["h1"]["stored_value"], "Rashford ")
        self.assertEqual(rows["h1"]["predicted_value"], "Rashford")
        self.assertEqual(rows["h2"]["finding"], "FILLED_ORPHAN")
        self.assertEqual(rows["h2"]["stored_value"], "Minjae ")
        self.assertNotIn("h3", rows)
        self.assertEqual(out["orphans"]["player_filter"], {"Minjae ": 1})


class BuilderRawEmissionTests(unittest.TestCase):
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
        with open("task-configs.json", "w") as fh:
            json.dump({"_meta": {"generated_at": "2026-10-03T12:00:00"},
                       "player": {"keyword_rules": [
                           {"canonical_value": "Rashford", "safe_keywords": ["rashford"]},
                           {"canonical_value": "Kim Min-jae",
                            "safe_keywords": ["minjae", "kim min-jae"]}],
                           "allowed_values": ["Rashford", "Kim Min-jae"]}}, fh)
        with open("cache.jsonl", "w") as fh:
            fh.write("")
        self.products = [
            _p("h1", "Rashford ", "Rashford Home Jersey"),
            _p("h2", "Minjae ", "Minjae Kim Away Jersey"),
            _p("h3", "Kim Min-jae", "Minjae Kim Home Jersey"),
        ]
        self.patches = [
            patch.object(mpl.config, "TASK_CONFIGS", "task-configs.json"),
            patch.object(mpl.config, "AUDIT_IGNORE", "missing.json"),
            patch.object(mpl.config, "CACHE_PRODUCTS_JSONL", "cache.jsonl", create=True),
            patch.object(mpl.config, "paste_dir", lambda: "paste", create=True),
            patch.object(mpl, "load_products", lambda *a, **k: self.products),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self.prev)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_raw_expected_current_and_whitespace_rows(self):
        with open("delta.json", "w") as fh:
            json.dump([{"canonical_value": "Rashford", "safe_keywords": ["marcus rashford"]}], fh)
        out = mpl.build_patch("delta.json", "player", cached=True, log=lambda *a: None)
        payload = json.load(open(os.path.join(out, "orphan-normalize.json")))
        self.assertIn({"handle": "h1", "expected_current": "Rashford ",
                       "set_to": "Rashford"}, payload)
        self.assertIn({"handle": "h2", "expected_current": "Minjae ",
                       "set_to": "Kim Min-jae"}, payload)
        self.assertEqual(len(payload), 2)
        csv_lines = open(os.path.join(out, "orphan-normalize.csv")).read().splitlines()
        self.assertEqual(csv_lines[0], "Handle,current_value,set_to,has_whitespace")
        self.assertIn('h1,"Rashford ","Rashford",yes', csv_lines)


if __name__ == "__main__":
    unittest.main()
