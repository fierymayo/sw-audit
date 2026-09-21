import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import report
from catalog import load_products
from matcher import compile_rules, match_title, normalize, strip_fc
from passes import (pass_addon_coverage, pass_filled_check, pass_fill_rates, pass_filter_blanks,
                    pass_forecast, pass_vendor_audit)
from rules import config_lint, load_task_rules

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
NOW = datetime.datetime(2026, 9, 21, 7, 0, tzinfo=datetime.timezone.utc)


def load_fixture_state():
    cfg = load_task_rules(os.path.join(FIXTURES, "test-config.json"))
    products = load_products(os.path.join(FIXTURES, "sample.jsonl"))
    return cfg, products


class TestMatcher(unittest.TestCase):
    def test_normalize_parity(self):
        self.assertEqual(normalize("Lautaro Martínez"), "lautaro martinez")
        self.assertEqual(normalize("Müller / Straße"), "muller strasse")
        self.assertEqual(normalize("Jerseys & Gear"), "jerseys and gear")
        self.assertEqual(normalize("St. Louis City-SC (Home)"), "st louis city sc home")

    def test_longest_wins(self):
        compiled = compile_rules([
            {"canonical_value": "Martinez", "safe_keywords": ["lautaro martinez"]},
            {"canonical_value": "Inter Miami CF", "safe_keywords": ["inter"]},
        ])
        best, ambiguous = match_title("Inter Lautaro Martinez Jersey", compiled)
        self.assertEqual(best, "Martinez")
        self.assertFalse(ambiguous)

    def test_equal_length_tie_is_ambiguous(self):
        compiled = compile_rules([
            {"canonical_value": "TieA", "safe_keywords": ["zzzz"]},
            {"canonical_value": "TieB", "safe_keywords": ["yyyy"]},
        ])
        best, ambiguous = match_title("Legends zzzz yyyy Scarf", compiled)
        self.assertIsNone(best)
        self.assertTrue(ambiguous)

    def test_padded_word_boundaries(self):
        compiled = compile_rules([{"canonical_value": "Son", "safe_keywords": ["son"]}])
        self.assertEqual(match_title("Season Opener Jersey", compiled), (None, False))
        self.assertEqual(match_title("Son Heung Min Jersey", compiled)[0], "Son")

    def test_strip_fc(self):
        self.assertEqual(strip_fc("leon fc"), "leon")
        self.assertEqual(strip_fc("villarreal cf"), "villarreal")


class TestReasonEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.products = load_fixture_state()
        cls.blanks = pass_filter_blanks(cls.products, cls.cfg, lag_hours=48, now=NOW)
        cls.by_handle = {r["handle"]: r for r in cls.blanks["player_filter"]}
        cls.club_by_handle = {r["handle"]: r for r in cls.blanks["club_filter"]}

    def reason(self, handle):
        return self.by_handle[handle]["reason"]

    def test_sync_miss_vs_lag(self):
        self.assertEqual(self.reason("p1"), "SYNC_MISS")
        self.assertEqual(self.reason("p2"), "SYNC_LAG")

    def test_keyword_gap_on_bare_surname(self):
        self.assertEqual(self.reason("p3"), "KEYWORD_GAP")
        self.assertEqual(self.by_handle["p3"]["matched_value"], "Davies")

    def test_accent_fold_matches(self):
        self.assertEqual(self.reason("p4"), "SYNC_MISS")
        self.assertEqual(self.by_handle["p4"]["matched_value"], "Martinez")

    def test_no_signal_not_flagged(self):
        self.assertNotIn("p5", self.by_handle)
        self.assertNotIn("p5", self.club_by_handle)

    def test_ambiguous(self):
        self.assertEqual(self.reason("p6"), "AMBIGUOUS")

    def test_invalid_choice(self):
        self.assertEqual(self.club_by_handle["p7"]["reason"], "INVALID_CHOICE")

    def test_no_rule_value_from_catalog_vocab(self):
        self.assertEqual(self.club_by_handle["p11"]["reason"], "NO_RULE_VALUE")
        self.assertEqual(self.club_by_handle["p11"]["matched_value"], "Tigres UANL")

    def test_inactive_ignored(self):
        for rows in self.blanks.values():
            self.assertNotIn("p10", {r["handle"] for r in rows})


class TestOtherPasses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.products = load_fixture_state()

    def test_fill_rates_and_diff(self):
        s = pass_fill_rates(self.products, self.cfg)
        self.assertEqual(s["active"], 14)
        self.assertEqual(s["fill_club_filter"]["filled"], 3)
        self.assertEqual(s["fill_sibling_products"]["filled"], 2)
        self.assertEqual(s["type_misc_active"], 1)
        prev = dict(s, fill_club_filter={"filled": 2, "pct": 8.3}, active=13)
        s2 = pass_fill_rates(self.products, self.cfg, prev=prev, prev_name="prev.json")
        self.assertEqual(s2["delta_vs_previous"]["fill_club_filter"]["filled"], 1)
        self.assertEqual(s2["delta_vs_previous"]["active"], 1)

    def test_sibling_precision_counts_multi_member_groups_only(self):
        # group_x has p13 (blank) + p15 (filled); group_solo and group_lonely have one member each
        scope = pass_fill_rates(self.products, self.cfg)["sibling_scope"]
        self.assertEqual(scope, {"products_with_group_tag": 3, "multi_member_groups": 1,
                                 "products_in_multi_member_groups": 2,
                                 "filled_in_multi_member_groups": 1})

    def test_addon_coverage(self):
        cov = pass_addon_coverage(self.products, self.cfg)
        self.assertEqual([r["handle"] for r in cov["missing_tag"]], ["p6"])
        self.assertEqual([r["handle"] for r in cov["stale_tag"]], ["p9"])

    def test_vendor_audit(self):
        v = pass_vendor_audit(self.products, self.cfg)
        findings = {r["vendor"]: r for r in v["rows"]}
        self.assertEqual(findings["NIKE"]["finding"], "NOT_NORMALIZED")
        self.assertEqual(findings["NIKE"]["suggested"], "Nike")
        self.assertEqual(findings["Kwik-Goal"]["finding"], "CASING_ALIAS_CANDIDATE")
        self.assertEqual(findings["Kwik-Goal"]["suggested"], "Kwik Goal")
        self.assertEqual(findings["soccerwearhouse.com"]["finding"], "NOT_NORMALIZED")
        self.assertEqual(findings["soccerwearhouse.com"]["suggested"], "Soccer Wearhouse")

    def test_lint_flags_invalid_choice_config(self):
        findings = config_lint(self.cfg)
        errors = [f for f in findings if f["level"] == "ERROR"]
        self.assertTrue(any("Leon FC" in f["message"] for f in errors))


class TestFilledCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.products = load_fixture_state()
        cls.result = pass_filled_check(cls.products, cls.cfg)
        cls.club = {r["handle"]: r for r in cls.result["rows"]["club_filter"]}

    def test_orphan_rows_and_counts(self):
        row = self.club["p12"]
        self.assertEqual((row["finding"], row["stored_value"], row["predicted_value"]),
                         ("FILLED_ORPHAN", "Tigres UANL", ""))
        self.assertEqual(self.result["orphans"]["club_filter"], {"Tigres UANL": 1})
        self.assertEqual(self.result["orphans"]["country_filter"], {"Wales": 1})
        self.assertEqual(self.result["orphans"]["player_filter"], {})

    def test_divergent(self):
        row = self.club["p14"]
        self.assertEqual((row["finding"], row["stored_value"], row["predicted_value"]),
                         ("FILLED_DIVERGENT", "Inter Miami CF", "FC Barcelona"))

    def test_agreeing_and_unpredicted_values_are_not_flagged(self):
        self.assertNotIn("p1", self.club)
        self.assertEqual(self.result["rows"]["player_filter"], [])

    def test_skipped_in_fallback_mode(self):
        self.assertIsNone(pass_filled_check(self.products, None))


class TestForecast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.products = load_fixture_state()

    def forecast(self, rules):
        return pass_forecast(self.products, self.cfg, "player", rules)

    def test_delta_rule_fills_known_blank(self):
        out = self.forecast([{"canonical_value": "Jordan Davies", "safe_keywords": ["jordan davies"]}])
        row = out["rows"][0]
        self.assertEqual((row["canonical"], row["keyword_count"], row["blanks_filled"], row["flags"]),
                         ("Jordan Davies", 1, 1, ""))
        self.assertEqual(row["sample_title"], "Jordan Davies Wales Tee")
        self.assertEqual((out["new_rules"], out["blanks_filled"], out["blanks"]), (1, 1, 13))

    def test_existing_longer_keyword_still_steals(self):
        # "home" also hits p1, but existing "messi" is longer and wins there
        out = self.forecast([{"canonical_value": "Home Guy", "safe_keywords": ["home"]}])
        self.assertEqual(out["rows"][0]["blanks_filled"], 2)

    def test_collision_with_existing_keyword_is_flagged_and_ambiguous(self):
        out = self.forecast([{"canonical_value": "Lionel", "safe_keywords": ["messi"]}])
        row = out["rows"][0]
        self.assertIn("COLLISION_EXISTING(messi -> Messi)", row["flags"])
        self.assertEqual(row["blanks_filled"], 0)

    def test_duplicate_within_delta_is_flagged(self):
        out = self.forecast([{"canonical_value": "A1", "safe_keywords": ["zed"]},
                             {"canonical_value": "B1", "safe_keywords": ["zed"]}])
        flags = {r["canonical"]: r["flags"] for r in out["rows"]}
        self.assertEqual(flags["A1"], "DUPLICATE_IN_DELTA(zed also in B1)")
        self.assertEqual(flags["B1"], "DUPLICATE_IN_DELTA(zed also in A1)")

    def test_needs_live_rules(self):
        self.assertIsNone(pass_forecast(self.products, None, "player", [{"canonical_value": "X"}]))


class TestSnapshot(unittest.TestCase):
    def test_writes_md_with_deltas_only_when_previous_exists(self):
        cfg, products = load_fixture_state()
        summary = pass_fill_rates(products, cfg)
        summary["filled_orphans"] = pass_filled_check(products, cfg)["orphans"]
        prev = dict(summary, active=13, fill_club_filter={"filled": 2, "pct": 8.3})
        with tempfile.TemporaryDirectory() as td, mock.patch.object(config, "OUT_DIR", td):
            cache = os.path.join(td, "products.jsonl")
            open(cache, "w").close()
            path = report.write_snapshot_md(summary, None, cache, log=lambda *_: None)
            self.assertEqual(os.path.basename(path), f"catalog-snapshot-{summary['generated_at'][:10]}.md")
            with open(path, encoding="utf-8") as fh:
                first = fh.read()
            self.assertIn("| Active products | 14 |  |", first)
            self.assertIn("- club_filter: Tigres UANL ×1", first)
            self.assertIn("Sibling precision: 1 of 2 products in multi-member groups filled (50.0%)", first)
            self.assertIn(f"rules file {summary['rules_file_date']}", first)
            self.assertIn("cache products.jsonl", first)

            report.write_snapshot_md(summary, prev, cache, log=lambda *_: None)
            with open(path, encoding="utf-8") as fh:
                second = fh.read()
            self.assertIn("| Active products | 14 | +1 |", second)
            self.assertIn("| club_filter | 3 | ", second)


class TestRulesFileDate(unittest.TestCase):
    def test_prefers_generated_at(self):
        cfg = load_task_rules(os.path.join(FIXTURES, "test-config.json"))
        self.assertEqual(cfg["_meta"]["file_date"], "2026-09-21")

    def test_falls_back_to_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "cfg.json")
            with open(path, "w") as fh:
                json.dump({}, fh)
            ts = datetime.datetime(2025, 3, 4, 12).timestamp()
            os.utime(path, (ts, ts))
            self.assertEqual(load_task_rules(path)["_meta"]["file_date"], "2025-03-04")


class TestGenerator(unittest.TestCase):
    def test_parses_export_shape(self):
        import make_task_configs as gen
        export = {
            "id": "6af0e5de-4ae5-4163-9bed-cd120471b60a",
            "name": "Sync player",
            "options": {
                "metafield_key__string_required": "player_filter",
                "write_mode__string_required": "add_only",
                "keyword_rules_json__multiline_required":
                    json.dumps([{"canonical_value": "Messi", "safe_keywords": ["messi"]}]),
                "allowed_values_json__multiline": json.dumps(["Messi"]),
            },
        }
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "player.json")
            out = os.path.join(td, "out.json")
            with open(src, "w") as fh:
                json.dump(export, fh)
            cfg = gen.build([src], out_path=out)
            self.assertEqual(len(cfg["player"]["keyword_rules"]), 1)
            self.assertEqual(cfg["player"]["write_mode"], "add_only")
            self.assertTrue(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
