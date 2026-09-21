from collections import Counter

import config

# Verbatim port of the SubCat Mechanic task's classification logic.
# Parity rules that must not be "improved":
# - most checks are plain SUBSTRING `in` on the lowercased title (not word-boundary)
# - title_words (only for " gk " checks) removes apostrophes without a space and does
#   NOT collapse whitespace
# - the pre-wrap asymmetry is a live task bug this simulator exists to surface:
#   "pre wrap"/"pre-wrap" reach Sock-Tape only under Misc, never under Accessories.
#   Do NOT fix it here.

_WORD_PUNCT = "-/_.,()[]:;&"


def _title_words(title_dc):
    s = title_dc.replace("\u2019", "").replace("'", "")
    for ch in _WORD_PUNCT:
        s = s.replace(ch, " ")
    return " " + s + " "


def classification_type(product_type, type_map):
    current = (product_type or "").strip()
    current_dc = current.lower()
    for key, value in (type_map or {}).items():
        if current_dc == key.strip().lower():
            return value.strip()
    return current


def classify(title, ctype):
    t = (title or "").strip().lower()

    def has(*words):
        return any(w in t for w in words)

    if ctype == "Air Freshener":
        return "SubCat_Air-Freshener"
    if ctype == "Enamel Pin":
        return "SubCat_Enamel-Pin"
    if ctype == "Decal / Sticker":
        if not has("panini", "sticker album", "sticker packet", "sticker box"):
            return "SubCat_Decal"
        return None
    if ctype == "Scarves":
        return "SubCat_Scarf"
    if ctype == "Mini Figures":
        return "SubCat_Mini-Figure"
    if ctype == "Posters":
        return "SubCat_Poster"
    if ctype == "Water Bottles":
        return "SubCat_Water-Bottle"
    if ctype == "Shin Guards":
        return "SubCat_Shin-Guards"
    if ctype == "Field Player Gloves":
        return "SubCat_Field-Gloves"
    if ctype == "Goalkeeper Gloves":
        return "SubCat_GK-Gloves"

    if ctype == "Accessories":
        if has("magnet"):
            return "SubCat_Magnet"
        if has("lanyard"):
            return "SubCat_Lanyard"
        if has("keychain", "key chain", "key ring", "keyring"):
            return "SubCat_Keychain"
        if has("armband", "captain"):
            return "SubCat_Armband"
        if has("glove wash"):
            return "SubCat_Glove-Wash"
        if has("grip spray", "magic grip"):
            return "SubCat_Grip-Spray"
        if has("tape", "kinesiology"):
            if not has("hat", "snapback", "cap"):
                return "SubCat_Sock-Tape"
            return None
        if has("pump"):
            if not has("heat", "jumpman"):
                return "SubCat_Ball-Pump"
            return None
        if has("lace", "shoelace"):
            return None
        if has("tactic", "clipboard"):
            return None
        if has("patch"):
            return None
        if has("guard lock", "guard stay", "shin guard sleeve"):
            return None
        if has("headband"):
            return None
        if has("neck warmer", "neckwarmer", "snood"):
            return None
        if has("mouthguard", "mouth guard"):
            return None
        if has("training vest", "practice vest"):
            return None
        if has("flag"):
            return None
        if has("nameset"):
            return None
        if has("referee", "wallet"):
            return None
        return "SubCat_Accessory-Other"

    if ctype == "Hats":
        if has("snapback"):
            return "SubCat_Hat-Snapback"
        if has("beanie", "knit"):
            return "SubCat_Hat-Beanie"
        if has("bucket"):
            return "SubCat_Hat-Bucket"
        return "SubCat_Hat"

    if ctype == "Socks":
        if has("grip", "trusox", "grip sox"):
            return "SubCat_Grip-Socks"
        if has("sand", "beach", "tilos", "tilo"):
            return "SubCat_Sand-Socks"
        return "SubCat_Soccer-Socks"

    if ctype == "Bags":
        if has("sackpack", "gymsack", "gym sack"):
            return "SubCat_Sackpack"
        if has("shoe bag", "cleat bag"):
            return "SubCat_Cleat-Bag"
        if has("ball bag", "mesh bag"):
            return "SubCat_Ball-Bag"
        if has("backpack"):
            return "SubCat_Backpack"
        if has("duffel", "duffle"):
            return "SubCat_Duffel"
        return "SubCat_Bag-Other"

    if ctype == "Balls":
        if has("mini"):
            return "SubCat_Ball-Mini"
        if has("futsal"):
            return "SubCat_Ball-Futsal"
        if has("training", "club"):
            return "SubCat_Ball-Training"
        if has("match", "pro", "official"):
            return "SubCat_Ball-Match"
        return "SubCat_Ball"

    if ctype == "Footwear":
        if has("slide", "sandal", "adilette"):
            return "SubCat_Slides"
        return None

    if ctype == "Soccer Collectibles":
        if has("funko"):
            return "SubCat_Funko-Pop"
        if has("trading card", "panini", "topps", "adrenalyn", "match attax"):
            return "SubCat_Trading-Card"
        return "SubCat_Collectible"

    if ctype == "Misc":
        if has("funko"):
            return "SubCat_Funko-Pop"
        if has("minix", "mini figure"):
            return "SubCat_Mini-Figure"
        if has("trading card", "panini", "topps"):
            return "SubCat_Trading-Card"
        if has("glove wash"):
            return "SubCat_Glove-Wash"
        if has("grip spray", "magic grip"):
            return "SubCat_Grip-Spray"
        if has("armband", "captain"):
            return "SubCat_Armband"
        if has("tape", "pre wrap", "pre-wrap", "kinesiology"):
            if not has("hat", "snapback", "cap"):
                return "SubCat_Sock-Tape"
            return None
        if has("action figure", "banbotoys", "bus figure"):
            return "SubCat_Mini-Figure"
        if has("ball pump", "essential pump", "hyperspeed pump"):
            return "SubCat_Ball-Pump"
        if has("air freshener"):
            return "SubCat_Air-Freshener"
        return None

    words = _title_words(t)
    if ctype == "Shorts":
        if has("goalkeeper") or " gk " in words or has("padded"):
            return "SubCat_GK-Shorts"
        return None
    if ctype == "Pants":
        if has("goalkeeper") or " gk " in words:
            return "SubCat_GK-Pants"
        return None
    if ctype == "Compression":
        if has("goalkeeper") or " gk " in words or has("padded"):
            return "SubCat_GK-Padded"
        return None

    return None


SIM_ROW_FIELDS = ["handle", "title", "type", "classification_type", "finding",
                  "predicted", "existing", "predicted_eligible", "currently_eligible",
                  "has_addon_tag", "admin_url", "storefront_url"]


def pass_subcat_sim(products, cfg):
    subcat_cfg = (cfg or {}).get("subcat") or {}
    type_map = subcat_cfg.get("product_type_map") or {}
    eligible = set(subcat_cfg.get("addon_eligible_subcats") or [])
    if not type_map or not eligible:
        return None
    rows = []
    for p in products:
        if p["status"] != "ACTIVE":
            continue
        ctype = classification_type(p["type"], type_map)
        predicted = classify(p["title"], ctype)
        existing = [t for t in p["tags"] if "SubCat_" in t]
        if predicted and not existing:
            finding = "MISSING_SUBCAT"
        elif predicted and predicted not in existing:
            finding = "WILL_REPLACE"
        elif predicted and len(existing) > 1:
            finding = "WILL_REPLACE"
        elif not predicted and existing:
            finding = "UNPREDICTED_EXISTING"
        else:
            continue
        rows.append({
            "handle": p["handle"], "title": p["title"], "type": p["type"],
            "classification_type": ctype, "finding": finding,
            "predicted": predicted or "", "existing": "|".join(existing),
            "predicted_eligible": bool(predicted and predicted in eligible),
            "currently_eligible": any(t in eligible for t in existing),
            "has_addon_tag": "addon-eligible" in p["_tags_lc"],
            "admin_url": config.admin_product_url(p["id"]),
            "storefront_url": config.storefront_product_url(p["handle"]),
        })
    return {"rows": rows, "counts": dict(Counter(r["finding"] for r in rows))}