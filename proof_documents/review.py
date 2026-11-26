"""Turns a reading + the INE data into checks and a proposed verdict. It never
verifies a component: the backend rule or a person does (credit-assessment PRD §13)."""
import calendar
import re
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from agents.id_document.curp import clean_curp
from proof_documents.matching import MATCH, MISMATCH, MISSING, PARTIAL, match_address, match_name

MIN_CONFIDENCE = 0.7
_UTILITIES = {"ELECTRICITY", "WATER", "GAS", "INTERNET", "PHONE"}
_RFC = re.compile(r"^[A-ZÑ&]{4}\d{6}[A-Z0-9]{3}$")
_UUID = re.compile(r"^[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}$")

_TEXT = {
    "name": {MISMATCH: "El nombre no coincide con la identificación.",
             PARTIAL: "El nombre coincide solo en parte con la identificación.",
             MISSING: "Falta el nombre en el documento o en la identificación."},
    "address": {MISMATCH: "El domicilio no coincide con la identificación.",
                PARTIAL: "El domicilio coincide solo en parte con la identificación.",
                MISSING: "Falta el domicilio en el documento o en la identificación."},
    "curp": {MISMATCH: "La CURP no coincide con la identificación.",
             MISSING: "No se pudo comparar la CURP."},
    "rfc": {PARTIAL: "El RFC no concuerda con la CURP.", MISSING: "No se pudo leer el RFC."},
    "date": {MISMATCH: "El documento no es reciente.", PARTIAL: "La fecha del documento es futura.",
             MISSING: "No se pudo leer la fecha del documento."},
    "amounts": {MISMATCH: "Los totales no cuadran.", MISSING: "No se pudieron leer los totales."},
    "payFrequency": {MISMATCH: "La periodicidad no concuerda con las fechas del periodo.",
                     PARTIAL: "No se pudo confirmar la periodicidad con las fechas del periodo.",
                     MISSING: "No se pudo leer la periodicidad."},
}

# The agent's reading → the customerIncomeVerification names, and its monthly factor
# (average weeks per month, not ×4 / ×2; user 2026-11-26). Multiply before dividing.
_FREQUENCY = {"WEEKLY": "WEEKLY", "BIWEEKLY_14": "FOURTEEN_DAYS", "SEMIMONTHLY": "SEMI_MONTHLY", "MONTHLY": "MONTHLY"}
_MONTHLY_FACTOR = {"WEEKLY": (52, 12), "FOURTEEN_DAYS": (26, 12), "SEMI_MONTHLY": (2, 1), "MONTHLY": (1, 1)}


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _age(day: date | None, today: date, max_age_days: int) -> str:
    if day is None:
        return MISSING
    if day > today:
        return PARTIAL
    return MATCH if (today - day).days <= max_age_days else MISMATCH


def _bill_date(doc: dict, today: date) -> date | None:
    """Printed issue date, else the start of the billed month, else the deadline."""
    issued = _date(doc.get("issueDate")) or _date(f"{doc.get('billingMonth') or ''}-01")
    due = _date(doc.get("dueDate"))
    return issued or (min(due, today) if due and (due - today).days <= 45 else due)


def _period_fits(frequency: str, start: date, end: date) -> bool:
    days = (end - start).days + 1
    if frequency == "WEEKLY":
        return days == 7
    if frequency == "FOURTEEN_DAYS":
        return days == 14
    if frequency == "SEMI_MONTHLY":   # 1–15 / 16–end of month (13 days in February), or a 15–16 day cut
        month_end = calendar.monthrange(end.year, end.month)[1]
        halves = (start.year, start.month) == (end.year, end.month) and (
            (start.day == 1 and end.day == 15) or (start.day == 16 and end.day == month_end))
        return halves or days in (15, 16)
    return 28 <= days <= 31   # MONTHLY


def _frequency(frequency: str, start: date | None, end: date | None) -> str:
    if not frequency:
        return MISSING
    if start is None or end is None or end < start:
        return PARTIAL
    return MATCH if _period_fits(frequency, start, end) else MISMATCH


def monthly_net(net_per_period: float, frequency: str) -> float | None:
    """Net per period → monthly, rounded down to cents. A proposal for the person who verifies;
    the stored figure is computed by sp_customerIncomeVerifications."""
    if net_per_period <= 0 or frequency not in _MONTHLY_FACTOR:
        return None
    num, den = _MONTHLY_FACTOR[frequency]
    return float((Decimal(str(net_per_period)) * num / den).quantize(Decimal("0.01"), rounding=ROUND_FLOOR))


def _finish(doc: dict, checks: dict, right_document: bool, blocking: set[str]) -> dict:
    reasons = [_TEXT[k][v] for k, v in checks.items() if v in _TEXT.get(k, {})]
    confidence = min(1.0, max(0.0, float(doc.get("confidence") or 0.0)))
    if confidence < MIN_CONFIDENCE:
        reasons.append("La lectura del documento no es confiable.")
    if doc.get("signsOfEditing"):
        reasons.append("El documento muestra posibles señales de edición.")

    if not doc:
        verdict, reasons = "UNREADABLE", ["No se pudo leer el documento."]
    elif not right_document:
        verdict, reasons = "WRONG_DOCUMENT", ["El documento no es del tipo que se pidió."]
    elif any(checks.get(k) == MISMATCH for k in blocking):
        verdict = "MISMATCH"
    elif reasons:
        verdict = "REVIEW"
    else:
        verdict = "MATCH"
    return {"verdict": verdict, "checks": checks, "reasons": reasons, "confidence": confidence, "fields": doc}


def review_address(doc: dict, ine: dict, today: date, max_age_days: int) -> dict:
    address = match_address(doc, ine.get("address"))
    checks = {
        "name": match_name(doc.get("holderName"), ine.get("name")),
        "address": address.pop("overall"),
        "date": _age(_bill_date(doc, today), today, max_age_days),
    }
    result = _finish(doc, checks, doc.get("documentType") in _UTILITIES, {"name", "address"})
    return {**result, "addressParts": address}


def review_payroll(doc: dict, ine: dict, today: date, max_age_days: int) -> dict:
    """A payslip carries no home address (its postal code is the tax one): not compared."""
    doc = dict(doc)
    curp, _ = clean_curp(doc.get("curp"))
    ine_curp, _ = clean_curp(ine.get("curp"))
    rfc = re.sub(r"[\s-]", "", str(doc.get("rfc") or "")).upper()
    rfc = rfc if _RFC.match(rfc) else ""
    folio = re.sub(r"\s", "", str(doc.get("fiscalFolio") or "")).upper()
    gross, deductions, net = (float(doc.get(k) or 0) for k in ("grossMXN", "deductionsMXN", "netMXN"))
    frequency = _FREQUENCY.get(str(doc.get("payFrequency") or "").upper(), "")
    if doc:
        doc.update(curp=curp, rfc=rfc, fiscalFolio=folio if _UUID.match(folio) else "", payFrequency=frequency)

    checks = {
        "name": match_name(doc.get("workerName"), ine.get("name")),
        "curp": MISSING if not (curp and ine_curp) else MATCH if curp == ine_curp else MISMATCH,
        # The first 10 characters agree except for a few SAT-assigned exceptions: review, not mismatch.
        "rfc": MISSING if not (rfc and curp) else MATCH if rfc[:10] == curp[:10] else PARTIAL,
        "date": _age(_date(doc.get("payDate")), today, max_age_days),
        "amounts": MISSING if gross <= 0 or net <= 0 else MATCH if abs(gross - deductions - net) <= 0.05 else MISMATCH,
        "payFrequency": _frequency(frequency, _date(doc.get("periodStart")), _date(doc.get("periodEnd"))),
    }
    result = _finish(doc, checks, doc.get("documentType") == "PAYROLL", {"name", "curp"})
    # Only when the frequency agrees with the printed period: otherwise the person decides it.
    trusted = checks["payFrequency"] == MATCH and checks["amounts"] == MATCH
    return {**result, "monthlyNetEstimate": monthly_net(net, frequency) if trusted else None}
