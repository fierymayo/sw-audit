import datetime
import json
from collections import defaultdict

FILTERS = ["club_filter", "country_filter", "player_filter", "tournament_filter"]

MF_NAMESPACES = ("custom", "mechanic")

_PRODUCT_FIELDS = '''
        id
        handle
        title
        vendor
        productType
        tags
        status
        createdAt
        updatedAt
        onlineStoreUrl
        publishedAt
        variantsCount { count }
        metafields {
          edges { node { namespace key value } }
        }
        variants {
          edges {
            node {
              id
              inventoryQuantity
              inventoryPolicy
              inventoryItem { tracked }
            }
          }
        }'''

PRODUCTS_BULK_QUERY = '''
{
  products {
    edges {
      node {%s
      }
    }
  }
}''' % _PRODUCT_FIELDS

PRODUCTS_BULK_QUERY_PUBS = '''
{
  products {
    edges {
      node {%s
        resourcePublications {
          edges {
            node {
              publication { id }
            }
          }
        }
      }
    }
  }
}''' % _PRODUCT_FIELDS


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
    var_by_parent = defaultdict(lambda: [0, False])
    pubs_by_parent = defaultdict(set)
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
                    "online_store_url": obj.get("onlineStoreUrl") or "",
                    "published_at": obj.get("publishedAt") or "",
                    "variants_count": ((obj.get("variantsCount") or {}).get("count") or 0),
                }
            elif parent and "key" in obj:
                if obj.get("namespace", "custom") in MF_NAMESPACES:
                    mf_by_parent[parent][obj["key"]] = obj.get("value") or ""
            elif parent and oid.startswith("gid://shopify/ProductVariant/"):
                agg = var_by_parent[parent]
                agg[0] += 1
                tracked = (obj.get("inventoryItem") or {}).get("tracked")
                if (tracked is False or obj.get("inventoryPolicy") == "CONTINUE"
                        or (obj.get("inventoryQuantity") or 0) > 0):
                    agg[1] = True
            elif parent and "publication" in obj:
                pub = (obj.get("publication") or {}).get("id")
                if pub:
                    pubs_by_parent[parent].add(pub)
    for gid, p in products.items():
        mfs = mf_by_parent.get(gid, {})
        raw = {}
        for k in FILTERS:
            raw[k] = mfs.get(k) or ""
            p[k] = raw[k].strip()
        p["_raw"] = raw
        p["_mf"] = mfs
        p["_tags_lc"] = [t.lower() for t in p["tags"]]
        p["variants_n"], p["sellable"] = var_by_parent.get(gid, (0, False))
        p["_pubs"] = pubs_by_parent.get(gid, set())
    return list(products.values())
