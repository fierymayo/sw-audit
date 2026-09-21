import datetime
import json
from collections import defaultdict

FILTERS = ["club_filter", "country_filter", "player_filter", "tournament_filter"]

PRODUCTS_BULK_QUERY = '''
{
  products {
    edges {
      node {
        id
        handle
        title
        vendor
        productType
        tags
        status
        createdAt
        updatedAt
        metafields(namespace: "custom") {
          edges { node { key value } }
        }
      }
    }
  }
}'''


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_products(jsonl_path):
    products = {}
    mf_by_parent = defaultdict(dict)
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            oid = obj.get("id", "")
            parent = obj.get("__parentId")
            if oid.startswith("gid://shopify/Product/"):
                products[oid] = {
                    "gid": oid,
                    "id": oid.rsplit("/", 1)[-1],
                    "handle": obj.get("handle", ""),
                    "title": obj.get("title", "") or "",
                    "vendor": obj.get("vendor", "") or "",
                    "type": obj.get("productType", "") or "",
                    "tags": obj.get("tags", []) or [],
                    "status": obj.get("status", "") or "",
                    "created_at": _parse_dt(obj.get("createdAt")),
                    "updated_at": _parse_dt(obj.get("updatedAt")),
                }
            elif parent and "key" in obj:
                mf_by_parent[parent][obj["key"]] = obj.get("value") or ""
    for gid, p in products.items():
        mfs = mf_by_parent.get(gid, {})
        for k in FILTERS:
            p[k] = (mfs.get(k) or "").strip()
        p["_mf"] = mfs
        p["_tags_lc"] = [t.lower() for t in p["tags"]]
    return list(products.values())
