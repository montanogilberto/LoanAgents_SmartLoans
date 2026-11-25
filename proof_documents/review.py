"""Turns a reading + the INE data into checks and a proposed verdict. It never
verifies a component: the backend rule or a person does (credit-assessment PRD §13)."""
import re
from datetime import date

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
}


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
    if doc:
        doc.update(curp=curp, rfc=rfc, fiscalFolio=folio if _UUID.match(folio) else "")

    checks = {
        "name": match_name(doc.get("workerName"), ine.get("name")),
        "curp": MISSING if not (curp and ine_curp) else MATCH if curp == ine_curp else MISMATCH,
        # The first 10 characters agree except for a few SAT-assigned exceptions: review, not mismatch.
        "rfc": MISSING if not (rfc and curp) else MATCH if rfc[:10] == curp[:10] else PARTIAL,
        "date": _age(_date(doc.get("payDate")), today, max_age_days),
        "amounts": MISSING if gross <= 0 or net <= 0 else MATCH if abs(gross - deductions - net) <= 0.05 else MISMATCH,
    }
    return _finish(doc, checks, doc.get("documentType") == "PAYROLL", {"name", "curp"})
