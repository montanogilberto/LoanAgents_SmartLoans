"""Name and address comparison against the INE. Plain code: same input, same answer."""
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

MATCH, PARTIAL, MISMATCH = "MATCH", "PARTIAL", "MISMATCH"
MISSING = "MISSING"   # one of the two sides has nothing to compare
TYPO_RATIO = 0.85

_PARTICLES = {"DE", "DEL", "LA", "LAS", "LOS", "EL", "Y"}
_STREET_TYPES = {"C", "CALLE", "AV", "AVE", "AVENIDA", "BLVD", "BOULEVARD", "PRIV", "PRIVADA", "CDA", "CERRADA",
                 "CALZ", "CALZADA", "AND", "ANDADOR", "CTO", "CIRCUITO", "PROL", "PROLONGACION", "NUM", "NO", "NUMERO",
                 "EXT", "INT", "INTERIOR", "EXTERIOR"}
_COLONY_MARK = re.compile(r"\b(?:COL|COLONIA|FRACC|FRACCIONAMIENTO|BARRIO|U HAB|UNIDAD HABITACIONAL|RES|RESIDENCIAL)\b")
_DIRECTIONS = {"OTE": "ORIENTE", "PTE": "PONIENTE", "NTE": "NORTE"}
_DIRECTION_WORDS = {"ORIENTE", "PONIENTE", "NORTE", "SUR"}


def normalize(text) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", plain).strip()


def _words(text) -> list[str]:
    return [w for w in normalize(text).split() if w not in _PARTICLES]


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= TYPO_RATIO


def match_name(document, ine) -> str:
    """Order-free: the INE prints surnames first, most documents given names first."""
    doc, ref = _words(document), _words(ine)
    if not doc or not ref:
        return MISSING
    if Counter(doc) == Counter(ref):
        return MATCH
    short, pool = (doc, list(ref)) if len(doc) <= len(ref) else (ref, list(doc))
    exact = 0
    pending = []
    for word in short:
        if word in pool:
            pool.remove(word)
            exact += 1
        else:
            pending.append(word)
    for word in pending:   # one misread letter, or an abbreviation (MA. / MARIA)
        near = next((p for p in pool if _similar(word, p)
                     or (min(len(word), len(p)) <= 2 and (word.startswith(p) or p.startswith(word)))), None)
        if near is None:
            return MISMATCH
        pool.remove(near)
    return PARTIAL if exact >= 2 else MISMATCH


def _tokens(text) -> list[str]:
    spaced = re.sub(r"(?<=\d)(?=[A-Z])|(?<=[A-Z])(?=\d)", " ", normalize(text))
    return [_DIRECTIONS.get(t, t) for t in spaced.split() if t not in _PARTICLES and t not in _STREET_TYPES]


def _coverage(wanted: list[str], pool: list[str]) -> str:
    if not wanted:
        return MISSING
    hits = sum(any(w == p or (len(w) > 3 and _similar(w, p)) for p in pool) for w in wanted)
    return MATCH if hits == len(wanted) else PARTIAL if hits * 2 > len(wanted) else MISMATCH


def _number(text) -> list[str]:
    return [t.lstrip("0") or "0" if t.isdigit() else t for t in _tokens(text)]


def match_address(doc: dict, ine_address) -> dict:
    """One result per part plus `overall`. Every part must agree for a MATCH."""
    ref = normalize(ine_address)
    doc_cp = re.sub(r"\D", "", str(doc.get("postalCode") or ""))
    # The INE prints the postal code after the neighborhood, so the last 5-digit group is it.
    cps = list(re.finditer(r"\b\d{5}\b", ref))
    ref_cp = cps[-1].group() if cps else ""
    local = ref[:cps[-1].start()] if cps else ref   # drops city and state
    mark = _COLONY_MARK.search(local)
    ref_street = local[:mark.start()] if mark else local
    ref_colony = local[mark.end():] if mark else local

    street_words = [t for t in _tokens(doc.get("street")) if not t.isdigit()]
    ref_words = _tokens(ref_street)
    street = MISSING if not ref_words else _coverage(street_words or _tokens(doc.get("street")), ref_words)
    doc_dir, ref_dir = _DIRECTION_WORDS & set(street_words), _DIRECTION_WORDS & set(ref_words)
    if doc_dir and ref_dir and doc_dir != ref_dir:
        street = MISMATCH
    elif street == MATCH and doc_dir != ref_dir:
        street = PARTIAL

    exterior, interior = _number(doc.get("exteriorNumber")), _number(doc.get("interiorNumber"))
    ref_number = _number(ref_street)
    ref_digits = [t for t in ref_number if t.isdigit()]
    ext_digits = [t for t in exterior if t.isdigit()]
    if not ext_digits or not ref_digits:
        number = MISSING
    elif not all(d in ref_digits for d in ext_digits):
        number = MISMATCH
    else:
        doc_unit = Counter(exterior + interior)
        doc_unit.update(t for t in _number(doc.get("street")) if t.isdigit())
        ref_unit = Counter(t for t in ref_number if t.isdigit() or len(t) == 1)
        number = MATCH if doc_unit == ref_unit else PARTIAL

    colony = MISSING if not _tokens(ref_colony) else _coverage(_tokens(doc.get("neighborhood")), _tokens(ref_colony))
    postal = MISSING if len(doc_cp) != 5 or not ref_cp else MATCH if doc_cp == ref_cp else MISMATCH

    parts = {"street": street, "number": number, "neighborhood": colony, "postalCode": postal}
    values = set(parts.values())
    if values == {MATCH}:
        overall = MATCH
    elif values == {MISSING}:
        overall = MISSING
    elif street == MISMATCH and not (doc_dir and ref_dir and doc_dir != ref_dir) \
            and number == colony == postal == MATCH:
        overall = PARTIAL   # everything else agrees: likely a misread street (seen live), a person looks
    elif MISMATCH in (street, number) or (postal == MISMATCH and colony != MATCH):
        overall = MISMATCH
    else:
        overall = PARTIAL
    return {**parts, "overall": overall}
