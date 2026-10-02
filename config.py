import datetime
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_VERSION = os.environ.get("API_VERSION", "2026-07")
COLLECTIONS_API_VERSION = "2026-07"
SHOP_DOMAIN = os.environ.get("SHOP_DOMAIN", "")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
STORE_SLUG = os.environ.get("STORE_SLUG", "soccer-wearhouse")
STOREFRONT = os.environ.get("STOREFRONT", "https://soccerwearhouse.com")

OUT_DIR = os.environ.get("OUT_DIR", "output")


def subdir(name):
    """Resolve an output subfolder against the CURRENT OUT_DIR and create it.
    Call-site resolution (not an import-time constant) so an OUT_DIR override
    still redirects everything."""
    path = os.path.join(OUT_DIR, name)
    os.makedirs(path, exist_ok=True)
    return path


def cache_dir():
    return subdir("caches")


def runs_dir():
    return subdir("runs")


def snapshots_dir():
    return subdir("snapshots")


def summaries_dir():
    return subdir("summaries")


def paste_dir():
    return subdir("paste")


CACHE_DIR = os.path.join(OUT_DIR, "caches")
RUNS_DIR = os.path.join(OUT_DIR, "runs")
SNAPSHOTS_DIR = os.path.join(OUT_DIR, "snapshots")
SUMMARIES_DIR = os.path.join(OUT_DIR, "summaries")
PASTE_DIR = os.path.join(OUT_DIR, "paste")

CACHE_PRODUCTS_JSONL = os.path.join(CACHE_DIR, "catalog-products.jsonl")
CACHE_COLLECTIONS_JSONL = os.path.join(CACHE_DIR, "catalog-collections.jsonl")
COLLECTIONS_BASELINE = os.path.join(SUMMARIES_DIR, "collections-baseline.json")
CACHE_COLLECTIONS_MEMBERS = os.path.join(CACHE_DIR, "catalog-collections-members.jsonl")
TOKEN_CACHE = os.path.join(CACHE_DIR, ".token.json")

STAMP_TZ = datetime.timezone(datetime.timedelta(hours=int(os.getenv("STAMP_UTC_OFFSET_HOURS", "5"))))

def now():
    return datetime.datetime.now(STAMP_TZ)

TASK_CONFIGS = os.environ.get("TASK_CONFIGS", "configs/task-configs.json")
AUDIT_IGNORE = "configs/audit-ignore.json"
HISTORY_DIR = "history"
METRICS_JSONL = os.path.join(HISTORY_DIR, "metrics.jsonl")
SYNC_LAG_HOURS = int(os.environ.get("SYNC_LAG_HOURS", "48"))


def run_dir(stamp):
    path = os.path.join(runs_dir(), f"audit-{stamp}")
    os.makedirs(path, exist_ok=True)
    return path

def admin_product_url(product_id):
    return f"https://admin.shopify.com/store/{STORE_SLUG}/products/{product_id}"


def admin_collection_url(collection_id):
    return f"https://admin.shopify.com/store/{STORE_SLUG}/collections/{collection_id}"


def storefront_product_url(handle):
    return f"{STOREFRONT}/products/{handle}"
