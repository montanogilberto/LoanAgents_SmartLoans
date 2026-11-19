"""CURP validation for the INE extraction (agents/id_document).

The CURP's 18th character is a check digit over the first 17 (RENAPO
algorithm), so a transcription can be verified without guessing: a reading
that passes the check digit is the printed CURP, one that fails is a misread
and is blanked. That lets the model hand back its best reading of a CURP that
sits on the anti-copy watermark instead of giving up on it.

The only "correction" applied is the O/0 and I/1 swap OCR makes, and only in
positions whose kind (letter vs digit) is fixed by the format — the result is
still accepted only if the check digit confirms it. Nothing is ever derived
from the name or birth date.
"""
import re

_DICT = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"
CURP_RE = re.compile(r"^[A-Z][AEIOUX][A-Z]{2}\d{6}[HMX][A-Z]{2}[B-DF-HJ-NP-TV-ZÑ]{3}[0-9A-Z]\d$")

# Position kind per index: L = letter, D = digit, A = either (homoclave), C = check digit.
_KINDS = "LLLLDDDDDDLLLLLLAC"
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"}
_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z"}


def check_digit(curp17: str) -> int:
    total = sum(_DICT.index(ch) * (18 - i) for i, ch in enumerate(curp17[:17]))
    digit = 10 - total % 10
    return 0 if digit == 10 else digit


def is_valid_curp(curp: str) -> bool:
    return bool(CURP_RE.match(curp)) and check_digit(curp) == int(curp[17])


def _normalize(raw) -> str:
    return re.sub(r"[\s\-.]", "", str(raw or "")).upper()


def _fix_kinds(curp: str) -> str:
    out = []
    for ch, kind in zip(curp, _KINDS):
        if kind in ("D", "C"):
            out.append(_TO_DIGIT.get(ch, ch))
        elif kind == "L":
            out.append(_TO_LETTER.get(ch, ch))
        else:
            out.append(ch)
    return "".join(out)


def _birth_yymmdd(fecha_nacimiento) -> str | None:
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", str(fecha_nacimiento or ""))
    if not m:
        return None
    day, month, year = m.groups()
    return f"{year[2:]}{int(month):02d}{int(day):02d}"


def clean_curp(raw, fecha_nacimiento="") -> tuple[str, list[str]]:
    """(curp, flagged) — the CURP when it passes the format and check digit,
    else "" with "curp" flagged so the person types it. When it is valid but its
    birth date disagrees with fechaNacimiento, both are kept and flagged: one of
    the two was misread and a human must pick."""
    value = _normalize(raw)
    if not value:
        return "", []
    if len(value) != 18:
        return "", ["curp"]
    if not is_valid_curp(value):
        fixed = _fix_kinds(value)
        if not is_valid_curp(fixed):
            return "", ["curp"]
        value = fixed
    birth = _birth_yymmdd(fecha_nacimiento)
    if birth and value[4:10] != birth:
        return value, ["curp", "fechaNacimiento"]
    return value, []
