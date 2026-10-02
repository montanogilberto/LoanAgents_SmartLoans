"""Deterministic supplier/product matching for the ticket extraction step.

The ticket agent transcribes names; this module maps them onto the company's
real suppliers/products. It is plain code (no LLM) so it can never invent an
id, and every decision is reproducible and unit-testable. It only SUGGESTS:
nothing here creates a supplier or product — a NEW status tells the UI to
offer "Nuevo proveedor" / "Agregar producto", and a person confirms.
"""
import re
import unicodedata
from difflib import SequenceMatcher

MATCHED = "MATCHED"        # one clearly best existing record
AMBIGUOUS = "AMBIGUOUS"    # plausible candidates but no clear winner — ask the user
NEW = "NEW"                # nothing plausible — offer to create it
UNAVAILABLE = "UNAVAILABLE"  # lookup failed or no name to match; caller decides

MATCH_THRESHOLD = 0.85
CANDIDATE_THRESHOLD = 0.60
# Best must beat the runner-up by this much to auto-pick (else AMBIGUOUS).
LEAD_MARGIN = 0.10
MAX_CANDIDATES = 3
# Must exceed LEAD_MARGIN so an active supplier beats an identically-named
# inactive one instead of the pair coming back AMBIGUOUS.
INACTIVE_FACTOR = 1 - LEAD_MARGIN - 0.01

_LEGAL_SUFFIXES = re.compile(
    r"\b(?:s a p i de c v|s a de c v|s de r l de c v|s de r l|sapi de cv|sa de cv"
    r"|s a p i|s a s|s a|sas|sa|cv|s c|a c)\s*$"
)
_STOPWORDS = {"de", "del", "la", "el", "los", "las", "y", "en", "con", "para"}
_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|mls|lts?|l|litros?|kgs?|kilos?|grs?|g|gramos?)\b")
_UNIT_TO_BASE = {  # canonical base unit: ml for volume, g for mass
    "ml": ("ml", 1), "mls": ("ml", 1),
    "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000), "litro": ("ml", 1000), "litros": ("ml", 1000),
    "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "gramo": ("g", 1), "gramos": ("g", 1),
    "kg": ("g", 1000), "kgs": ("g", 1000), "kilo": ("g", 1000), "kilos": ("g", 1000),
}


def _base_normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    text = text.lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9.,]+", " ", text).strip()


def _sizes(text: str) -> tuple[set[str], str]:
    """Pulls quantity+unit tokens ("8.5 L", "500ml") out as canonical
    "8500ml"-style sizes and returns (sizes, text_without_sizes). Sizes are
    kept separate because "Ariel 8.5 L" and "Ariel 4 L" are different
    products even though their words are identical."""
    found: set[str] = set()

    def _take(m: re.Match) -> str:
        base, factor = _UNIT_TO_BASE[m.group(2)]
        value = float(m.group(1).replace(",", ".")) * factor
        found.add(f"{value:g}{base}")
        return " "

    return found, _SIZE.sub(_take, text)


def normalize_supplier(name: str) -> list[str]:
    text = re.sub(r"[.,]", " ", _base_normalize(name))
    text = re.sub(r"\s+", " ", text).strip()
    prev = None
    while prev != text:
        prev, text = text, _LEGAL_SUFFIXES.sub("", text).strip()
    return [t for t in text.split() if t not in _STOPWORDS]


def _tokens_match(a: str, b: str) -> bool:
    if a == b:
        return True
    # Thermal tickets abbreviate ("DET", "LIQ"): accept alphabetic prefixes.
    return min(len(a), len(b)) >= 3 and a.isalpha() and b.isalpha() and (a.startswith(b) or b.startswith(a))


def _overlap(a: list[str], b: list[str]) -> int:
    pool, hits = list(b), 0
    for tok in a:
        for i, other in enumerate(pool):
            if _tokens_match(tok, other):
                hits += 1
                del pool[i]
                break
    return hits


def _score(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    hits = _overlap(a, b)
    jaccard = hits / (len(a) + len(b) - hits)
    containment = hits / min(len(a), len(b))
    seq = max(
        SequenceMatcher(None, "".join(a), "".join(b)).ratio(),
        SequenceMatcher(None, "".join(sorted(a)), "".join(sorted(b))).ratio(),
    )
    return round(0.4 * seq + 0.3 * jaccard + 0.3 * containment, 4)


def _decide(scored: list[tuple[float, dict, str]], id_key: str) -> dict:
    scored.sort(key=lambda t: t[0], reverse=True)
    candidates = [
        {"id": rec.get(id_key), "name": label, "score": score}
        for score, rec, label in scored[:MAX_CANDIDATES] if score >= CANDIDATE_THRESHOLD
    ]
    if not candidates:
        return {"status": NEW, "id": None, "name": "", "score": scored[0][0] if scored else 0.0, "candidates": []}
    best = candidates[0]
    runner_up = candidates[1]["score"] if len(candidates) > 1 else 0.0
    if best["score"] >= MATCH_THRESHOLD and best["score"] - runner_up >= LEAD_MARGIN:
        return {"status": MATCHED, "id": best["id"], "name": best["name"], "score": best["score"], "candidates": candidates}
    return {"status": AMBIGUOUS, "id": None, "name": "", "score": best["score"], "candidates": candidates}


def _supplier_score(a: list[str], b: list[str]) -> float:
    """_score plus a supplier-only floor: a registered name is often the
    ticket's brand plus a suffix ("Sam's Club" / "SAMS CLUB MEXICO",
    "Walmart" / "Wal-Mart de México"). If every word of the shorter name is
    in the longer one (2+ words), or the shorter's letters appear contiguously
    in the longer's and span 5+ chars, floor the score at the match threshold.
    LEAD_MARGIN still blocks it when two suppliers fit equally. Not used for
    products, where "Coca Cola" must not match "Coca Cola Light 600 ml"."""
    score = _score(a, b)
    if not a or not b:
        return score
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 2 and _overlap(short, long_) == len(short):
        return max(score, MATCH_THRESHOLD + 0.01)
    short_c, long_c = "".join(short), "".join(long_)
    if len(short_c) >= 5 and short_c in long_c:
        return max(score, MATCH_THRESHOLD + 0.01)
    return score


def match_supplier(name: str, suppliers: list[dict]) -> dict:
    """Maps the merchant name read off the ticket to one of the company's
    suppliers. Active suppliers win ties over inactive ones."""
    wanted = normalize_supplier(name)
    if not wanted:
        return {"status": UNAVAILABLE, "id": None, "name": "", "score": 0.0, "candidates": []}
    scored = []
    for s in suppliers:
        label = str(s.get("supplierName") or "")
        score = _supplier_score(wanted, normalize_supplier(label))
        if str(s.get("active", "1")) == "0":
            score = round(score * INACTIVE_FACTOR, 4)
        scored.append((score, s, label))
    return _decide(scored, "supplierId")


def _product_tokens(name: str) -> tuple[set[str], list[str]]:
    sizes, rest = _sizes(re.sub(r"(\d)\s*,\s*(\d)", r"\1.\2", _base_normalize(name)))
    tokens = [t for t in re.sub(r"[.,]", " ", rest).split() if t not in _STOPWORDS]
    return sizes, tokens


def match_product(name: str, products: list[dict]) -> dict:
    """Maps a ticket line description to one of the company's products. If
    both names state a size and the sizes differ, the pair is capped below
    the candidate threshold (different pack sizes are different products)."""
    wanted_sizes, wanted = _product_tokens(name)
    if not wanted:
        return {"status": UNAVAILABLE, "id": None, "name": "", "score": 0.0, "candidates": []}
    scored = []
    for p in products:
        label = str(p.get("name") or "")
        sizes, tokens = _product_tokens(label)
        score = _score(wanted + sorted(wanted_sizes), tokens + sorted(sizes))
        if wanted_sizes and sizes and wanted_sizes.isdisjoint(sizes):
            score = min(score, CANDIDATE_THRESHOLD - 0.01)
        scored.append((score, p, label))
    return _decide(scored, "productId")
