"""What to REGISTER from a read ticket — decided here, not in the POS app.

The extraction agent transcribes the ticket and matching.py maps names onto the
company's real suppliers/products. This module turns that into the operation the
cashier will review: for the supplier and for every line, "use" an existing
record, "create" a new one, or "choose" (the person must pick). It also gives a
verdict on whether the reading is clean enough to need no review at all.

Pure functions over the response as a plain dict (camelCase keys, exactly what
POST /expenses/extract-ticket returns) — no I/O, no Gemini, nothing invented:
an ambiguous or unavailable match is NEVER auto-selected. The POS app only
displays this and applies the person's edits.
"""
from agents.ticket_extraction.matching import MATCHED, NEW, AMBIGUOUS

USE = "use"
CREATE = "create"
CHOOSE = "choose"

MIN_CONFIDENCE = 0.7
# Same one-peso tolerance reconcile.py uses for the ticket's own arithmetic.
TOTAL_TOLERANCE = 1.0


def _round2(value: float) -> float:
    return round(float(value) * 100) / 100


def _quantity(line: dict) -> float:
    q = float(line.get("quantity") or 0)
    return q if q > 0 else 1.0


def unit_cost(line: dict) -> float:
    """Purchase cost PER UNIT: the printed unit price when there is one, else
    the line total divided by the quantity."""
    unit_price = float(line.get("unitPrice") or 0)
    if unit_price > 0:
        return _round2(unit_price)
    quantity = float(line.get("quantity") or 0)
    total = float(line.get("lineTotal") or 0)
    return _round2(total / quantity if quantity > 0 else total)


def _candidates(match: dict) -> list[dict]:
    return [
        {"id": c.get("id"), "name": c.get("name", ""), "score": c.get("score", 0.0)}
        for c in (match.get("candidates") or [])
    ]


def build_proposal(response: dict) -> dict:
    merchant = str(response.get("merchantName") or "").strip()
    supplier_match = response.get("supplierMatch") or {}

    if supplier_match.get("status") == MATCHED and supplier_match.get("id"):
        supplier = {"action": USE, "supplierId": supplier_match["id"],
                    "name": supplier_match.get("name") or merchant}
    elif supplier_match.get("status") == NEW and merchant:
        supplier = {"action": CREATE, "supplierId": None, "name": merchant}
    else:
        supplier = {"action": CHOOSE, "supplierId": None, "name": merchant}
    supplier["candidates"] = _candidates(supplier_match)

    lines = []
    for index, line in enumerate(response.get("lineItems") or []):
        match = line.get("productMatch") or {}
        name = str(line.get("name") or "").strip()
        if match.get("status") == MATCHED and match.get("id"):
            action, product_id, final_name = USE, match["id"], match.get("name") or name
        elif match.get("status") == NEW:
            action, product_id, final_name = CREATE, None, name
        else:
            action, product_id, final_name = CHOOSE, None, name
        lines.append({
            "index": index,
            "ticketName": str(line.get("name") or ""),
            "quantity": _quantity(line),
            "unitCost": unit_cost(line),
            "lineTotal": _round2(line.get("lineTotal") or 0),
            "action": action,
            "productId": product_id,
            "name": final_name,
            "candidates": _candidates(match),
            "needsReview": bool(line.get("needsReview")),
        })

    total = float(response.get("total") or 0)
    return {
        "supplier": supplier,
        "lines": lines,
        "paymentMethod": response.get("paymentMethod") or "",
        "paymentDate": response.get("ticketDate") or "",
        "ticketTotal": _round2(total) if total > 0 else 0.0,
    }


def evaluate(response: dict, proposal: dict | None = None) -> dict:
    """{"ready": bool, "reasons": [Spanish reasons a person still has to look]}.
    Ready only when every field needed to register the egreso was read AND
    matched to an existing record."""
    if not response.get("isPurchaseTicket"):
        return {"ready": False, "reasons": ["La imagen no parece un ticket de compra."]}

    proposal = proposal or build_proposal(response)
    reasons: list[str] = []

    supplier_status = (response.get("supplierMatch") or {}).get("status")
    if supplier_status == AMBIGUOUS:
        reasons.append("Hay varios proveedores posibles.")
    elif supplier_status == NEW:
        reasons.append(f"El proveedor «{response.get('merchantName') or ''}» no está registrado.")
    elif proposal["supplier"]["action"] != USE:
        reasons.append("No se pudo identificar el proveedor.")

    lines = proposal["lines"]
    if not lines:
        reasons.append("No se leyeron productos.")
    else:
        unmatched = sum(1 for l in lines if l["action"] != USE)
        if unmatched == 1:
            reasons.append("1 producto no está en el catálogo o hay dudas.")
        elif unmatched > 1:
            reasons.append(f"{unmatched} productos no están en el catálogo o hay dudas.")

    if not proposal["paymentDate"]:
        reasons.append("No se leyó la fecha.")
    if not proposal["paymentMethod"]:
        reasons.append("No se identificó el método de pago (Efectivo, Tarjeta o Transferencia).")
    total = proposal["ticketTotal"]
    if not total > 0:
        reasons.append("No se leyó el total.")
    elif lines and response.get("totalMatchesItems") is not True:
        reasons.append("Los productos no suman el total del ticket.")
    if float(response.get("confidence") or 0) < MIN_CONFIDENCE:
        reasons.append("El agente tiene poca confianza en la lectura.")
    if response.get("needsReview") or response.get("issues"):
        reasons.append("El agente marcó puntos por revisar.")

    # What would actually be saved (unit cost × quantity of the matched lines)
    # must agree with the ticket's own total.
    if not reasons:
        saved = _round2(sum(_round2(l["quantity"] * l["unitCost"]) for l in lines if l["action"] == USE))
        if abs(saved - total) > TOTAL_TOLERANCE:
            reasons.append("El total calculado de los productos difiere del total del ticket.")

    return {"ready": not reasons, "reasons": reasons}
