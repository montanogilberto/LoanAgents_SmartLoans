"""Deterministic checks on what the model read: a value that fails here is cleared
and reported, so the cashier types it rather than trusting a bad reading."""
import re
from datetime import date

MIN_CONFIDENCE = 0.7
_CLAVE = re.compile(r"^[A-Z0-9]{6,40}$")


def review_extraction(result: dict, today: date) -> dict:
    issues = [str(i) for i in result.get("issues") or []]
    is_receipt = bool(result.get("isTransferReceipt"))
    confidence = float(result.get("confidence") or 0.0)

    try:
        amount = round(float(result.get("amountMXN") or 0), 2)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        amount = 0.0
        issues.append("No se pudo leer el total.")

    clave = re.sub(r"[\s-]", "", str(result.get("claveRastreo") or "")).upper()
    if not _CLAVE.match(clave):
        clave = ""
        issues.append("No se pudo leer la clave de rastreo.")

    transfer_date = str(result.get("transferDate") or "")
    try:
        if date.fromisoformat(transfer_date) > today:
            transfer_date = ""
            issues.append("La fecha del comprobante es futura.")
    except ValueError:
        transfer_date = ""
        issues.append("No se pudo leer la fecha.")

    bank = str(result.get("bankFrom") or "").strip()
    if not bank:
        issues.append("No se pudo leer el banco.")

    return {
        "isTransferReceipt": is_receipt, "confidence": confidence, "amountMXN": amount,
        "claveRastreo": clave, "transferDate": transfer_date, "bankFrom": bank,
        "issues": list(dict.fromkeys(issues)),
        "needsReview": not is_receipt or confidence < MIN_CONFIDENCE or not (amount and clave and transfer_date and bank),
    }
