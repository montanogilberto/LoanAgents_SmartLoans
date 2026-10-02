"""Deterministic checks on what the ticket agent read.

The model transcribes; this code does the arithmetic and date sanity checks,
so a misread digit surfaces as an issue for the reviewer instead of silently
pre-filling the form. Pure functions — no I/O, no Gemini.
"""
from datetime import date, datetime, timedelta, timezone

# Totals/lines within one peso count as matching (same tolerance the
# transfer-evidence agent uses for amounts).
TOLERANCE = 1.0

# UTC-7, no DST — same "today" convention as tools/backend_api.py.
_HERMOSILLO_OFFSET = timedelta(hours=7)

# The POS expense form's "Método de Pago" only offers these three values, and
# smartloans_backend's ledger (journalEntries.cash_or_bank_code) keys off them:
# Efectivo -> Caja 1101, Tarjeta/Transferencia -> Bancos 1105. Anything else
# would not match the dropdown, so those types map to "" (the person picks).
# paymentType keeps the finer distinction (credit vs. debit, cheque, ...).
PAYMENT_LABELS = {
    "EFECTIVO": "Efectivo",
    "TARJETA_CREDITO": "Tarjeta",
    "TARJETA_DEBITO": "Tarjeta",
    "TARJETA": "Tarjeta",
    "TRANSFERENCIA": "Transferencia",
    "CHEQUE": "",
    "OTRO": "",
    "NO_VISIBLE": "",
}


def _num(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _today(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return (now - _HERMOSILLO_OFFSET).date()


def payment_label(payment_type: str) -> str:
    return PAYMENT_LABELS.get(str(payment_type or "").upper(), "")


def reconcile(result: dict, now: datetime | None = None) -> dict:
    """Returns {"issues": [...], "itemsTotal": float, "totalMatchesItems": bool | None,
    "ticketDate": str, "needsReview": bool}. `ticketDate` is blanked when it is
    not a valid, non-future date, since a bad date must not pre-fill the form."""
    issues: list[str] = []
    items = [i for i in result.get("lineItems", []) if isinstance(i, dict)]
    total = _num(result.get("total"))
    subtotal = _num(result.get("subtotal"))
    tax = _num(result.get("tax"))

    items_total = round(sum(_num(i.get("lineTotal")) for i in items), 2)
    total_matches: bool | None = None

    if items and total > 0:
        candidates = [total]
        if subtotal > 0:
            candidates.append(subtotal)
        if tax > 0:
            candidates.append(total - tax)
        total_matches = any(abs(items_total - c) <= TOLERANCE for c in candidates)
        if not total_matches:
            issues.append(
                f"La suma de los productos (${items_total:,.2f}) no coincide con "
                f"el total del ticket (${total:,.2f})."
            )
    elif result.get("isPurchaseTicket") and total <= 0:
        issues.append("No se pudo leer el total del ticket.")

    if subtotal > 0 and tax > 0 and total > 0 and abs(subtotal + tax - total) > TOLERANCE:
        issues.append(
            f"Subtotal (${subtotal:,.2f}) + impuesto (${tax:,.2f}) no es igual "
            f"al total (${total:,.2f})."
        )

    for item in items:
        qty, unit, line = _num(item.get("quantity")), _num(item.get("unitPrice")), _num(item.get("lineTotal"))
        name = str(item.get("name") or "").strip() or "(sin nombre)"
        if qty > 0 and unit > 0 and abs(qty * unit - line) > TOLERANCE:
            issues.append(f"'{name}': {qty:g} x ${unit:,.2f} no es igual a ${line:,.2f}.")
        if item.get("needsReview"):
            issues.append(f"'{name}': renglón poco legible, revisar.")

    ticket_date = str(result.get("ticketDate") or "")
    if ticket_date:
        try:
            parsed = date.fromisoformat(ticket_date)
        except ValueError:
            issues.append("La fecha del ticket no tiene un formato válido.")
            ticket_date = ""
        else:
            if parsed > _today(now) + timedelta(days=1):
                issues.append("La fecha del ticket está en el futuro.")
                ticket_date = ""

    unreadable = result.get("unreadableFields", []) or []
    if unreadable:
        issues.append("No se pudo leer: " + ", ".join(str(f) for f in unreadable) + ".")

    return {
        "issues": issues,
        "itemsTotal": items_total,
        "totalMatchesItems": total_matches,
        "ticketDate": ticket_date,
        "needsReview": bool(issues) or not result.get("isPurchaseTicket", False),
    }
