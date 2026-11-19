"""Turns an uploaded menu/catalog file into model input parts.

PDF and images go to Gemini as-is (it reads them natively). Text formats are
decoded. Excel .xlsx is a zip of XML: read with the standard library (no
openpyxl dependency) into tab-separated text. Legacy .xls is not supported.
Everything here is untrusted visitor input — size-capped and treated as data.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

from google.genai.types import Part

MAX_FILE_BYTES = 8_000_000
MAX_TEXT_CHARS = 30_000
MAX_SHEET_ROWS = 600

BINARY_TYPES = {
    "application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif", "image/gif",
}
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_EXT_TYPES = {
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "heic": "image/heic", "heif": "image/heif", "gif": "image/gif",
    "txt": "text/plain", "md": "text/markdown", "markdown": "text/markdown", "csv": "text/csv",
    "json": "application/json", "xlsx": XLSX_TYPE, "xls": "application/vnd.ms-excel",
}
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class MenuFileError(ValueError):
    """The file can't be read; the message is user-facing Spanish."""


def resolve_type(mime_type: str, file_name: str) -> str:
    """Browsers often send '' or octet-stream for .md/.xlsx — fall back to the extension."""
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime in BINARY_TYPES | TEXT_TYPES | {XLSX_TYPE}:
        return mime
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return _EXT_TYPES.get(ext, mime)


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "A")
    n = 0
    for ch in letters.group(0) if letters else "A":
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def xlsx_to_text(data: bytes) -> str:
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise MenuFileError("El archivo de Excel está dañado o no es .xlsx.")
    shared: list[str] = []
    if "xl/sharedStrings.xml" in book.namelist():
        root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    sheets = sorted(n for n in book.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    out: list[str] = []
    rows_left = MAX_SHEET_ROWS
    for name in sheets:
        out.append(f"## Hoja {name.rsplit('sheet', 1)[-1].removesuffix('.xml')}")
        root = ElementTree.fromstring(book.read(name))
        for row in root.iter(f"{{{_NS['m']}}}row"):
            if rows_left <= 0:
                break
            cells: dict[int, str] = {}
            for c in row.findall("m:c", _NS):
                kind = c.get("t")
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
                else:
                    v = c.find("m:v", _NS)
                    value = v.text if v is not None and v.text else ""
                    if kind == "s" and value.isdigit() and int(value) < len(shared):
                        value = shared[int(value)]
                if value.strip():
                    cells[_col_index(c.get("r", ""))] = value.strip()
            if cells:
                out.append("\t".join(cells.get(i, "") for i in range(max(cells) + 1)))
                rows_left -= 1
    text = "\n".join(out).strip()
    if not text:
        raise MenuFileError("El Excel no tiene datos que pueda leer.")
    return text


def file_to_parts(data: bytes, mime_type: str, file_name: str) -> list[Part]:
    if len(data) > MAX_FILE_BYTES:
        raise MenuFileError("El archivo es muy grande (máximo 8 MB).")
    mime = resolve_type(mime_type, file_name)
    if mime in BINARY_TYPES:
        return [Part.from_bytes(data=data, mime_type=mime)]
    if mime == XLSX_TYPE:
        return [Part(text=xlsx_to_text(data)[:MAX_TEXT_CHARS])]
    if mime == "application/vnd.ms-excel":
        raise MenuFileError("Excel antiguo (.xls) no es compatible. Guárdalo como .xlsx o .csv.")
    if mime in TEXT_TYPES:
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            raise MenuFileError("El archivo está vacío.")
        return [Part(text=text[:MAX_TEXT_CHARS])]
    raise MenuFileError("Formato no compatible. Usa PDF, imagen, Excel (.xlsx), CSV o Markdown.")
