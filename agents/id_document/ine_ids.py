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
