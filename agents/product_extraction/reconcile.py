"""Deterministic checks on the product label agent's output.

The model reads; code verifies. Nothing here calls Gemini or the network, so it
is fully unit-tested (tests/test_product_extraction_agent.py):
  * barcode   — digits only, valid GTIN length and check digit, else blanked
                (a one-digit misread would otherwise be saved as a real code)
  * netContent/unitOfMeasure — only the units the POS form offers, > 0
  * expirationDate — real ISO date; past dates kept but flagged
  * suggestedName — brand + product + variant + net content, for the form
"""
from __future__ import annotations

import re
from datetime import date

FORM_UNITS = {"g", "kg", "ml", "L", "pz"}
GTIN_LENGTHS = {8, 12, 13, 14}
LOW_CONFIDENCE = 0.6


def gtin_is_valid(code: str) -> bool:
    """GS1 check digit (EAN-8, UPC-A, EAN-13, GTIN-14)."""
    if not code.isdigit() or len(code) not in GTIN_LENGTHS:
        return False
    body, check = code[:-1], int(code[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def _clean_barcode(raw: str) -> tuple[str, str | None]:
    """(barcode or "", issue or None)."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return "", None
    if len(digits) not in GTIN_LENGTHS:
        return "", f"El código de barras leído ({digits}) no tiene una longitud válida; captúralo a mano."
    if not gtin_is_valid(digits):
        return "", f"El código de barras leído ({digits}) no pasa el dígito verificador; revísalo o escanéalo."
    return digits, None


def _fmt_amount(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def suggested_name(brand: str, product: str, variant: str, net: float, unit: str) -> str:
    """'Electrolit Suero Rehidratante Manzana 1000 ml' — the form's Nombre."""
    parts = [p.strip() for p in (brand, product, variant) if p and p.strip()]
    # Don't repeat the brand when the model already put it in the product name.
    if len(parts) >= 2 and parts[1].lower().startswith(parts[0].lower()):
        parts = parts[1:]
    if net > 0 and unit in FORM_UNITS:
        parts.append(f"{_fmt_amount(net)} {unit}")
    return " ".join(parts)


def reconcile(result: dict, today: date | None = None) -> dict:
    """Normalizes the model's fields and lists every problem found. Returns the
    corrected values plus `issues` (Spanish, for the reviewer) and needsReview."""
    today = today or date.today()
    issues: list[str] = []

    bar_code, bar_issue = _clean_barcode(str(result.get("barCode", "") or ""))
    if bar_issue:
        issues.append(bar_issue)

    try:
        net = float(result.get("netContent", 0.0) or 0.0)
    except (TypeError, ValueError):
        net = 0.0
    unit = str(result.get("unitOfMeasure", "") or "")
    if unit not in FORM_UNITS or net <= 0:
        if net > 0 and unit not in FORM_UNITS:
            issues.append("No se reconoció la unidad del contenido neto; selecciónala a mano.")
        net, unit = 0.0, ""

    expiration = str(result.get("expirationDate", "") or "").strip()
    if expiration:
        try:
            exp_date = date.fromisoformat(expiration)
        except ValueError:
            issues.append(f"La fecha de caducidad leída ({expiration}) no es válida; captúrala a mano.")
            expiration = ""
        else:
            if exp_date < today:
                issues.append(f"El producto aparece CADUCADO ({exp_date.strftime('%d/%m/%Y')}).")

    unreadable = [str(f) for f in result.get("unreadableFields", []) if f]
    for field in unreadable:
        issues.append(f"No se pudo leer «{field}» en la foto.")

    is_product = bool(result.get("isProduct", False))
    if not is_product:
        issues.append("La foto no parece ser de un producto.")

    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    brand = str(result.get("brand", "") or "").strip()
    product = str(result.get("productName", "") or "").strip()
    variant = str(result.get("variant", "") or "").strip()

    return {
        "isProduct": is_product,
        "confidence": confidence,
        "brand": brand,
        "productName": product,
        "variant": variant,
        "suggestedName": suggested_name(brand, product, variant, net, unit),
        "description": str(result.get("description", "") or "").strip(),
        "barCode": bar_code,
        "netContent": net,
        "unitOfMeasure": unit,
        "netContentRaw": str(result.get("netContentRaw", "") or ""),
        "expirationDate": expiration,
        "expirationRaw": str(result.get("expirationRaw", "") or ""),
        "lotNumber": str(result.get("lotNumber", "") or ""),
        "manufacturer": str(result.get("manufacturer", "") or ""),
        "unreadableFields": unreadable,
        "notes": [str(n) for n in result.get("notes", []) if n],
        "issues": issues,
        "needsReview": bool(issues) or not is_product or confidence < LOW_CONFIDENCE,
    }
