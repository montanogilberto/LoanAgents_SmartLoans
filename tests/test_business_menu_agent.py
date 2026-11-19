"""POS signup menu/catalog reader (runner mocked, no LLM call)."""
import base64
import io
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai.types import Content, Part


def _final(text):
    return SimpleNamespace(is_final_response=lambda: True, partial=False,
                           content=Content(role="model", parts=[Part(text=text)]), actions=None)


def _fake_runner(monkeypatch, text, seen=None):
    import main

    async def run_async(*, user_id, session_id, new_message, **_):
        if seen is not None:
            seen["parts"] = new_message.parts
        yield _final(text)
    monkeypatch.setattr(main._business_menu_runner, "run_async", run_async)
    main._menu_calls.clear()


def _xlsx(rows):
    shared, sheet_rows = [], []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(value, str):
                shared.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    return buf.getvalue()


def test_agent_registered_without_tools():
    import main
    from agents import business_menu_agent
    assert business_menu_agent.tools == []
    assert "/signup/business-menu" in {r.path for r in main.app.routes}


def test_xlsx_is_read_as_tab_separated_text():
    from agents.business_menu.extract import xlsx_to_text
    text = xlsx_to_text(_xlsx([["Producto", "Precio"], ["Lavado por kilo", 28], ["Planchado", 15.5]]))
    assert "Producto\tPrecio" in text and "Lavado por kilo\t28" in text and "Planchado\t15.5" in text


def test_type_falls_back_to_extension_and_rejects_xls():
    from agents.business_menu.extract import MenuFileError, file_to_parts, resolve_type
    assert resolve_type("application/octet-stream", "menu.md") == "text/markdown"
    assert file_to_parts(b"# Menu\nTacos $20", "", "menu.md")[0].text.startswith("# Menu")
    assert file_to_parts(b"%PDF-1.4", "application/pdf", "m.pdf")[0].inline_data.mime_type == "application/pdf"
    with pytest.raises(MenuFileError):
        file_to_parts(b"x", "application/vnd.ms-excel", "menu.xls")
    with pytest.raises(MenuFileError):
        file_to_parts(b"x", "application/zip", "menu.zip")


def test_route_filters_modules_bounds_items_and_sends_file(monkeypatch):
    seen = {}
    items = [{"name": f"Item {i}", "price": i} for i in range(70)]
    _fake_runner(monkeypatch, '{"readable": true, "businessType": "Taquería", "summary": "Vendes tacos.",'
                 ' "categories": [{"name": "Tacos", "items": %s}], "suggestedModules": ["pos_cart", "casino", "pos_cart"],'
                 ' "questions": ["a", "b", "c", "d"]}' % str(items).replace("'", '"'), seen)
    body = TestClient(__import__("main").app).post("/signup/business-menu", json={
        "businessName": "Tacos Don Pepe", "fileName": "menu.md", "mimeType": "",
        "fileBase64": base64.b64encode(b"Tacos al pastor $20").decode()}).json()
    assert body["readable"] and body["itemCount"] == 60
    assert body["suggestedModules"] == ["pos_cart"]
    assert len(body["questions"]) == 3
    assert '"Tacos Don Pepe"' in seen["parts"][0].text and "pastor" in seen["parts"][1].text


def test_unreadable_material_returns_no_items(monkeypatch):
    _fake_runner(monkeypatch, '{"readable": false, "summary": "Es una foto de un perro.",'
                 ' "categories": [{"name": "x", "items": [{"name": "y"}]}], "suggestedModules": ["pos_cart"]}')
    body = TestClient(__import__("main").app).post("/signup/business-menu", json={
        "fileName": "a.png", "mimeType": "image/png", "fileBase64": base64.b64encode(b"png").decode()}).json()
    assert body["readable"] is False and body["itemCount"] == 0 and body["suggestedModules"] == []


def test_bad_inputs():
    import main
    main._menu_calls.clear()
    c = TestClient(main.app)
    assert c.post("/signup/business-menu", json={}).status_code == 400
    assert c.post("/signup/business-menu", json={"fileName": "a.zip", "mimeType": "application/zip",
                                                  "fileBase64": base64.b64encode(b"z").decode()}).status_code == 415
    assert c.post("/signup/business-menu", json={"url": "http://127.0.0.1/menu"}).status_code == 422
