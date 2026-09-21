import re

# Exact port of the Liquid normalization in the club/country/player sync tasks.
# Do not "improve" this table — parity with the live tasks is the point.
_ACCENTS = {
    "á": "a", "à": "a", "â": "a", "ä": "a", "ã": "a", "å": "a",
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "í": "i", "ì": "i", "î": "i", "ï": "i",
    "ó": "o", "ò": "o", "ô": "o", "ö": "o", "õ": "o",
    "ú": "u", "ù": "u", "û": "u", "ü": "u",
    "ñ": "n", "ç": "c", "ß": "ss",
}
_PUNCT = "'’`-_/.,()[]+:;!?|"


def normalize(text):
    s = (text or "").lower().strip()
    for a, b in _ACCENTS.items():
        s = s.replace(a, b)
    s = s.replace("&", " and ")
    for ch in _PUNCT:
        s = s.replace(ch, " ")
    return re.sub(r"\s+", " ", s).strip()


def compile_rules(rules):
    compiled = []
    for rule in rules or []:
        canonical = (rule.get("canonical_value") or "").strip()
        if not canonical:
            continue
        for kw in rule.get("safe_keywords") or []:
            k = normalize(kw)
            if k:
                compiled.append((canonical, k, len(k)))
    return compiled


def match_title(title, compiled):
    """Returns (best_value, ambiguous). Longest keyword wins; an equal-length
    tie across different canonical values is ambiguous (task skips those)."""
    padded = " " + normalize(title) + " "
    best_value, best_len, ambiguous = None, 0, False
    for canonical, kw, klen in compiled:
        if " " + kw + " " in padded:
            if klen > best_len:
                best_len, best_value, ambiguous = klen, canonical, False
            elif klen == best_len and best_value != canonical:
                ambiguous = True
    if ambiguous:
        return None, True
    return best_value, False


def phrase_in(padded_title_norm, value_norm):
    return " " + value_norm + " " in padded_title_norm


def strip_fc(value_norm):
    s = re.sub(r"\b(fc|cf|afc|sc)\b", "", value_norm)
    return re.sub(r"\s+", " ", s).strip()
