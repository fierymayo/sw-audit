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
CACHE_PRODUCTS_JSONL = os.path.join(OUT_DIR, "catalog-products.jsonl")
CACHE_COLLECTIONS_JSONL = os.path.join(OUT_DIR, "catalog-collections.jsonl")
COLLECTIONS_BASELINE = os.path.join(OUT_DIR, "collections-baseline.json")
CACHE_COLLECTIONS_MEMBERS = os.path.join(OUT_DIR, "catalog-collections-members.jsonl")
TOKEN_CACHE = os.path.join(OUT_DIR, ".token.json")

STAMP_TZ = datetime.timezone(datetime.timedelta(hours=int(os.getenv("STAMP_UTC_OFFSET_HOURS", "5"))))

def now():
    return datetime.datetime.now(STAMP_TZ)

TASK_CONFIGS = os.environ.get("TASK_CONFIGS", "task-configs.json")
SYNC_LAG_HOURS = int(os.environ.get("SYNC_LAG_HOURS", "48"))


def run_dir(stamp):
    path = os.path.join(OUT_DIR, f"audit-{stamp}")
    os.makedirs(path, exist_ok=True)
    return path

def admin_product_url(product_id):
    return f"https://admin.shopify.com/store/{STORE_SLUG}/products/{product_id}"


def admin_collection_url(collection_id):
    return f"https://admin.shopify.com/store/{STORE_SLUG}/collections/{collection_id}"


def storefront_product_url(handle):
    return f"{STOREFRONT}/products/{handle}"
