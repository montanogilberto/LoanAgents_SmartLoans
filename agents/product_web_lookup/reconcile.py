"""Deterministic checks on what the web lookup agent returned.

The model searches and transcribes; this code normalizes units, decides whether
the result counts as verified (from the search's own sources, never the model's
word) and compares it with the breakdown printed on the ticket. Pure functions.
"""
from agents.product_web_lookup.schema import WebProductResult

POS_UNITS = {"g", "kg", "ml", "L", "pz"}
_UNIT_ALIASES = {
    "g": "g", "gr": "g", "grs": "g", "gramos": "g",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogramos": "kg",
    "ml": "ml", "mls": "ml", "mililitros": "ml",
    "l": "L", "lt": "L", "lts": "L", "litro": "L", "litros": "L",
    "pz": "pz", "pza": "pz", "pzas": "pz", "pieza": "pz", "piezas": "pz",
}
# Same size in both units: 1 L = 1000 ml, 1 kg = 1000 g.
_TO_BASE = {"ml": ("ml", 1.0), "L": ("ml", 1000.0), "g": ("g", 1.0), "kg": ("g", 1000.0)}


def normalize_unit(unit: str) -> str:
    return _UNIT_ALIASES.get(str(unit or "").strip().lower().rstrip("."), "")


def _same_size(a: float, unit_a: str, b: float, unit_b: str) -> bool:
    if unit_a not in _TO_BASE or unit_b not in _TO_BASE:
        return True  # nothing comparable (pz, or a unit missing): don't flag
    base_a, f_a = _TO_BASE[unit_a]
    base_b, f_b = _TO_BASE[unit_b]
    return base_a == base_b and abs(a * f_a - b * f_b) <= 0.01 * max(a * f_a, b * f_b)


def suggested_name(r: dict) -> str:
    """Name for the POS catalog: brand + product + variant + size of ONE piece
    ("Member's Mark Agua Purificada 500 ml"); the full title when not split."""
    words: list[str] = []
    for part in (r["brand"], r["productName"], r["variant"]):
        for word in str(part or "").split():
            # "Refresco Original" + variant "Original" → once.
            if word.lower() not in {w.lower() for w in words}:
                words.append(word)
    base = " ".join(words)
    if not r["productName"]:
        base = r["fullName"].strip()
    if base and r["netContent"] > 0 and r["unitOfMeasure"] and r["unitOfMeasure"] != "pz":
        size = f"{r['netContent']:g} {r['unitOfMeasure']}"
        if size.replace(" ", "").lower() not in base.replace(" ", "").lower():
            base = f"{base} {size}"
    return base[:200]


def _coerce(raw: dict) -> dict:
    """Field-by-field cast of the model's JSON to WebProductResult's types. The
    reply is free text (no output_schema), so null, "" or "500 ml" where a number
    or list belongs falls back to that field's default instead of failing it all."""
    out = {}
    for name, field in WebProductResult.model_fields.items():
        value, default = (raw or {}).get(name), field.get_default(call_default_factory=True)
        try:
            if value is None:
                out[name] = default
            elif isinstance(default, bool):
                out[name] = value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "si", "sí")
            elif isinstance(default, (int, float)):
                out[name] = type(default)(float(str(value).replace(",", ".").split()[0]))
            elif isinstance(default, list):
                out[name] = [str(v) for v in value] if isinstance(value, list) else ([str(value)] if str(value).strip() else [])
            else:
                out[name] = str(value)
        except (ValueError, IndexError):
            out[name] = default
    return out


def reconcile_web_lookup(raw: dict, sources: list[dict], ticket_pack: dict | None = None) -> dict:
    """Normalized result + {"verified", "sources", "suggestedName", "issues",
    "needsReview"}. verified = the model confirmed the code AND the search
    actually returned sources; a confirmation without sources is not trusted."""
    r = WebProductResult(**_coerce(raw)).model_dump()
    r["unitOfMeasure"] = normalize_unit(r["unitOfMeasure"])
    r["netContent"] = max(float(r["netContent"] or 0), 0.0)
    r["unitsPerPack"] = max(int(r["unitsPerPack"] or 1), 1)
    r["price"] = max(float(r["price"] or 0), 0.0)
    if not r["unitOfMeasure"]:
        r["netContent"] = 0.0
    # "detergentes en polvo" / "DETERGENTE" → "Detergentes en polvo" / "Detergente": one spelling per kind.
    kind = " ".join(r["productType"].split())[:50]
    r["productType"] = kind[:1].upper() + kind[1:].lower() if kind else ""
    issues = [str(n) for n in r.pop("notes") if str(n).strip()]

    if not r["found"]:
        return {**r, "codeConfirmed": False, "verified": False, "sources": sources,
                "suggestedName": "", "issues": issues or ["No se encontró el producto en internet."],
                "needsReview": True}

    verified = bool(r["codeConfirmed"] and sources)
    if not sources:
        issues.append("La búsqueda no devolvió fuentes; confirma los datos antes de usarlos.")
    elif not r["codeConfirmed"]:
        issues.append("No se encontró este código exacto en internet; el producto se identificó por la descripción.")

    pack = ticket_pack or {}
    t_units = int(pack.get("unitsPerPack") or 1)
    t_content, t_unit = float(pack.get("netContent") or 0), str(pack.get("unitOfMeasure") or "")
    if t_units > 1 and r["unitsPerPack"] != t_units:
        issues.append(f"El ticket dice {t_units} piezas por paquete e internet {r['unitsPerPack']}.")
    if t_content > 0 and r["netContent"] > 0 and not _same_size(t_content, t_unit, r["netContent"], r["unitOfMeasure"]):
        issues.append(f"El ticket dice {t_content:g} {t_unit} e internet {r['netContent']:g} {r['unitOfMeasure']}.")

    return {**r, "verified": verified, "sources": sources, "suggestedName": suggested_name(r),
            "issues": issues, "needsReview": not verified or bool(issues)}
