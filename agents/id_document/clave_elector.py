"""Clave de elector validation for the INE extraction (agents/id_document).

The clave de elector has a fixed 18-character layout:

    AAAAAA  YYMMDD  EE  S  NNN
    letters  birth  state sex homoclave
    (name)   date   01–32 H/M  3 digits
                    (87/88 = born abroad)

It has no check digit (unlike the CURP), but the layout alone exposes the
readings Gemini makes up from a blurry card — a 19-character "clave", letters
where the birth date goes, a month 13. Those are blanked and flagged so the
person types it. A well-formed one is cross-checked against the birth date and
the (check-digit-valid) CURP: a disagreement keeps the value but flags it, since
one of the two was misread and a human must pick.

Like curp.py, the only correction is the O/0 and I/1 swap OCR makes, in
positions whose kind (letter vs digit) the layout fixes. Nothing is derived
from the name or birth date.
"""
import re

CLAVE_ELECTOR_RE = re.compile(r"^[A-Z]{6}\d{6}(0[1-9]|[12]\d|3[0-2]|8[78])[HMX]\d{3}$")

# Position kind per index: L = letter, D = digit, S = sex (letter).
_KINDS = "LLLLLLDDDDDDDDSDDD"
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"}
_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z"}


def _normalize(raw) -> str:
    return re.sub(r"[\s\-.]", "", str(raw or "")).upper()


def _fix_kinds(value: str) -> str:
    out = []
    for ch, kind in zip(value, _KINDS):
        if kind == "D":
            out.append(_TO_DIGIT.get(ch, ch))
        elif kind in ("L", "S"):
            out.append(_TO_LETTER.get(ch, ch))
        else:
            out.append(ch)
    return "".join(out)


def _plausible_date(yymmdd: str) -> bool:
    month, day = int(yymmdd[2:4]), int(yymmdd[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31


def is_valid_clave_elector(value: str) -> bool:
    return bool(CLAVE_ELECTOR_RE.match(value)) and _plausible_date(value[6:12])


def _birth_yymmdd(fecha_nacimiento) -> str | None:
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", str(fecha_nacimiento or ""))
    if not m:
        return None
    day, month, year = m.groups()
    return f"{year[2:]}{int(month):02d}{int(day):02d}"


def clean_clave_elector(raw, fecha_nacimiento="", curp="") -> tuple[str, list[str]]:
    """(claveElector, flagged).

    - Not the 18-character layout (even after the O/0, I/1 fix): "" + ["claveElector"].
    - Valid, but its birth date disagrees with fechaNacimiento: kept, both flagged.
    - Valid, but birth date or sex disagrees with a valid CURP (pass `curp` only
      after clean_curp): kept, claveElector flagged.
    """
    value = _normalize(raw)
    if not value:
        return "", []
    if len(value) != 18:
        return "", ["claveElector"]
    if not is_valid_clave_elector(value):
        fixed = _fix_kinds(value)
        if not is_valid_clave_elector(fixed):
            return "", ["claveElector"]
        value = fixed

    flags: list[str] = []
    birth = _birth_yymmdd(fecha_nacimiento)
    if birth and value[6:12] != birth:
        flags += ["claveElector", "fechaNacimiento"]
    # CURP layout: YYMMDD at 4–9, sex at 10. The clave: YYMMDD at 6–11, sex at 14.
    if len(curp or "") == 18 and (curp[4:10] != value[6:12] or (value[14] in "HM" and curp[10] != value[14])):
        if "claveElector" not in flags:
            flags.append("claveElector")
    return value, flags
