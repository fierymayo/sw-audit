import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import catalog
import passes

P = "gid://shopify/Product/"
V = "gid://shopify/ProductVariant/"
GOOGLE = passes.GOOGLE_PUBLICATION
STORE = passes.STORE_PUBLICATION


def _prod(n, status="ACTIVE", tags=(), url="", sellable=True, variants_n=1,
          mf=None, pubs=()):
    return {"gid": P + str(n), "id": str(n), "handle": f"h{n}", "title": f"t{n}",
            "status": status, "tags": list(tags),
            "_tags_lc": [t.lower() for t in tags],
            "online_store_url": url, "published_at": "", "sellable": sellable,
            "variants_n": variants_n, "_mf": mf or {}, "_pubs": set(pubs),
            "club_filter": "", "country_filter": "", "player_filter": "",
            "tournament_filter": "", "_raw": {}}


NOW = datetime.datetime(2026, 10, 11, tzinfo=datetime.timezone.utc)


class LoaderChannelTests(unittest.TestCase):
    def test_variant_fold_mechanic_and_pubs(self):
        rows = [
            {"id": P + "1", "handle": "h1", "title": "t", "status": "ACTIVE"},
            {"id": V + "11", "__parentId": P + "1", "inventoryQuantity": 0,
             "inventoryPolicy": "DENY", "inventoryItem": {"tracked": True}},
            {"id": V + "12", "__parentId": P + "1", "inventoryQuantity": None,
             "inventoryPolicy": "CONTINUE", "inventoryItem": {"tracked": True}},
            {"id": "gid://shopify/Metafield/9", "__parentId": P + "1",
             "namespace": "mechanic", "key": "out_of_stock_at",
             "value": "2026-10-01T00:00:00Z"},
            {"id": "", "__parentId": P + "1", "publication": {"id": GOOGLE}},
            {"id": P + "2", "handle": "h2", "title": "t", "status": "ACTIVE",
             "onlineStoreUrl": "https://x/2"},
            {"id": V + "21", "__parentId": P + "2", "inventoryQuantity": 0,
             "inventoryPolicy": "DENY", "inventoryItem": {"tracked": True}},
            {"id": "gid://shopify/Metafield/8", "__parentId": P + "2",
             "namespace": "other_app", "key": "junk", "value": "x"},
        ]
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        try:
            prods = {p["handle"]: p for p in catalog.load_products(path)}
        finally:
            os.remove(path)
        self.assertTrue(prods["h1"]["sellable"])
        self.assertEqual(prods["h1"]["variants_n"], 2)
        self.assertEqual(prods["h1"]["_mf"]["out_of_stock_at"], "2026-10-01T00:00:00Z")
        self.assertEqual(prods["h1"]["_pubs"], {GOOGLE})
        self.assertFalse(prods["h2"]["sellable"])
        self.assertEqual(prods["h2"]["online_store_url"], "https://x/2")
        self.assertNotIn("junk", prods["h2"]["_mf"])


class ChannelPassTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.prev = os.getcwd()
        os.chdir(self.dir)
        self.p = patch.object(passes.config, "CHANNEL_STATE",
                              os.path.join(self.dir, "channel-state.json"),
                              create=True)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        os.chdir(self.prev)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_section_a_codes(self):
        products = [
            _prod(1, url="", sellable=True),
            _prod(2, tags=["addon-eligible"], url="", sellable=False),
            _prod(3, url="https://x/3", sellable=True,
                  mf={"out_of_stock_at": "2026-06-29T00:00:00Z"}),
            _prod(4, url="https://x/4", sellable=True, variants_n=251),
            _prod(5, status="DRAFT", url="", sellable=True),
        ]
        rows, counts, pubs = passes.pass_channel_checks(products, None, now=NOW)
        self.assertFalse(pubs)
        self.assertEqual(counts, {"CHANNEL_SELLABLE_OFF_STORE": 1,
                                  "CHANNEL_ADDON_OFF_STORE": 1,
                                  "CHANNEL_STALE_TIMESTAMP": 1,
                                  "CHANNEL_OVER_250_VARIANTS": 1})
        self.assertEqual(len(rows), 4)

    def test_marker_on_store_fires_on_second_audit_only(self):
        marker = json.dumps([STORE, GOOGLE])
        products = [_prod(1, url="https://x/1", sellable=True,
                          mf={"unpublished_by_task": marker})]
        _, c1, _ = passes.pass_channel_checks(products, None, now=NOW)
        self.assertNotIn("CHANNEL_MARKER_ON_STORE_ALL", c1)
        _, c2, _ = passes.pass_channel_checks(products, None, now=NOW + datetime.timedelta(days=1))
        self.assertEqual(c2.get("CHANNEL_MARKER_ON_STORE_ALL"), 1)

    def test_section_b_codes(self):
        products = [
            _prod(1, url="", sellable=False, pubs=[GOOGLE],
                  mf={"out_of_stock_at": "2026-09-20T00:00:00Z"}),
            _prod(2, url="https://x/2", sellable=True, pubs=[STORE]),
        ]
        rows, counts, pubs = passes.pass_channel_checks(products, None, now=NOW)
        self.assertTrue(pubs)
        self.assertEqual(counts.get("CHANNEL_GOOGLE_WITHOUT_STORE"), 1)
        self.assertEqual(counts.get("CHANNEL_OOS_ON_GOOGLE_PAST_THRESHOLD"), 1)
        self.assertNotIn("CHANNEL_SELLABLE_OFF_STORE", counts)

    def test_marker_mismatch_persistence(self):
        marker = json.dumps([GOOGLE])
        products = [_prod(1, url="https://x/1", sellable=True, pubs=[STORE],
                          mf={"unpublished_by_task": marker})]
        _, c1, _ = passes.pass_channel_checks(products, None, now=NOW)
        self.assertNotIn("CHANNEL_MARKER_MISMATCH", c1)
        _, c2, _ = passes.pass_channel_checks(products, None, now=NOW + datetime.timedelta(days=1))
        self.assertEqual(c2.get("CHANNEL_MARKER_MISMATCH"), 1)


    def test_marker_not_promoted_within_20h(self):
        marker = json.dumps([STORE, GOOGLE])
        products = [_prod(1, url="https://x/1", sellable=True,
                          mf={"unpublished_by_task": marker})]
        passes.pass_channel_checks(products, None, now=NOW)
        _, c2, _ = passes.pass_channel_checks(products, None, now=NOW + datetime.timedelta(hours=2))
        self.assertNotIn("CHANNEL_MARKER_ON_STORE_ALL", c2)

    def test_cached_run_does_not_write_state(self):
        marker = json.dumps([STORE, GOOGLE])
        products = [_prod(1, url="https://x/1", sellable=True,
                          mf={"unpublished_by_task": marker})]
        passes.pass_channel_checks(products, None, now=NOW, fresh=False)
        self.assertFalse(os.path.exists(passes.config.CHANNEL_STATE))
        _, c2, _ = passes.pass_channel_checks(products, None, now=NOW + datetime.timedelta(days=1))
        self.assertNotIn("CHANNEL_MARKER_ON_STORE_ALL", c2)


    def test_truncated_variants_guard(self):
        p = _prod(1, url="https://x/1", sellable=False, variants_n=100,
                  pubs=[GOOGLE], mf={"out_of_stock_at": "2026-09-01T00:00:00Z"})
        p["variants_count"] = 140
        rows, counts, _ = passes.pass_channel_checks([p], None, now=NOW)
        self.assertEqual(counts.get("CHANNEL_VARIANTS_TRUNCATED"), 1)
        self.assertNotIn("CHANNEL_OOS_ON_GOOGLE_PAST_THRESHOLD", counts)


if __name__ == "__main__":
    unittest.main()
