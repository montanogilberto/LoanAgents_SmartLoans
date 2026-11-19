"""INE back-side identifiers (RFC-015, POSVending docs/rfcs): CIC and OCR.

Both come from the machine-readable zone (the lines with <<<) on the BACK of
the credential. They are what an authorized INE "Lista Nominal" check uses to
confirm the credential is genuine and current, so a wrong digit is worse than
no value: anything that isn't exactly the expected digit count is blanked.

  * CIC — Código de Identificación de Credencial: 9 digits. Identifies this
    physical card (a replacement card gets a new CIC).
  * OCR — 13 digits. On models E–H the last 9 are the "identificador del
    ciudadano"; on older models it is the OCR number of the card.
"""
import re

CIC_LENGTH = 9
OCR_LENGTH = 13


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def clean_ine_ids(cic, ocr) -> tuple[str, str, list[str]]:
    """(cic, ocr, dropped) — values with the wrong digit count are blanked and
    their names returned in `dropped` (they go to lowConfidenceFields so the
    person types them by hand). Spaces/'<' fillers are tolerated."""
    dropped: list[str] = []
    cic_digits, ocr_digits = _digits(cic), _digits(ocr)
    if cic_digits and len(cic_digits) != CIC_LENGTH:
        dropped.append("cic")
        cic_digits = ""
    if ocr_digits and len(ocr_digits) != OCR_LENGTH:
        dropped.append("ocr")
        ocr_digits = ""
    return cic_digits, ocr_digits, dropped


def clean_vigencia(value) -> tuple[str, bool]:
    """('YYYY', dropped) — the LAST 4-digit year printed after "VIGENCIA" on the
    front (a range "2023 - 2033" means valid through Dec 31, 2033). Years
    outside 2000–2100 are blanked and flagged. Expiry itself is judged by the
    app/back end against today's date, not here."""
    years = [int(y) for y in re.findall(r"\d{4}", str(value or ""))]
    if not years:
        return "", bool(str(value or "").strip())
    year = max(years)
    if not 2000 <= year <= 2100:
        return "", True
    return str(year), False
