"""
Tests for supplier/product matching on extracted tickets: the pure matcher,
the company-scoped list tools, and the /expenses/extract-ticket wiring. No
Gemini and no real HTTP — the model reply and backend calls are stubbed.
"""
import base64
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.ticket_extraction.matching import (
    AMBIGUOUS, MATCHED, NEW, UNAVAILABLE, match_product, match_supplier,
)

SUPPLIERS = [
    {"supplierId": 1, "supplierName": "SAMS CLUB MEXICO S. DE R.L. DE C.V.", "active": "1"},
    {"supplierId": 2, "supplierName": "Wal-Mart de México SAB de CV", "active": "1"},
    {"supplierId": 3, "supplierName": "Ferretería El Tornillo", "active": "1"},
    {"supplierId": 4, "supplierName": "Ferretería La Central", "active": "1"},
]
PRODUCTS = [
    {"productId": 10, "name": "Detergente Líquido Ariel RevitaColor 8.5 L"},
    {"productId": 11, "name": "Detergente Líquido Ariel RevitaColor 4 L"},
    {"productId": 12, "name": "Suavizante de Telas Suavitel Fresca Primavera 8.5 L"},
    {"productId": 14, "name": "Coca Cola Light 600 ml"},
]


def test_supplier_matches_despite_legal_suffix_and_country():
    for name in ("Sam's Club", "SAMS CLUB MEXICO", "Sams Club SA de CV"):
        out = match_supplier(name, SUPPLIERS)
        assert (out["status"], out["id"]) == (MATCHED, 1), name
    assert match_supplier("Walmart", SUPPLIERS)["id"] == 2


def test_supplier_ambiguous_when_two_fit_equally():
    out = match_supplier("Ferretería", SUPPLIERS)
    assert out["status"] == AMBIGUOUS and out["id"] is None
    assert {c["id"] for c in out["candidates"]} == {3, 4}


def test_supplier_new_and_unavailable():
    assert match_supplier("Abarrotes Lupita", SUPPLIERS)["status"] == NEW
    assert match_supplier("", SUPPLIERS)["status"] == UNAVAILABLE
    assert match_supplier("Sam's Club", [])["status"] == NEW


def test_inactive_supplier_loses_to_active_duplicate():
    pool = [{"supplierId": 7, "supplierName": "Costco", "active": "0"},
            {"supplierId": 8, "supplierName": "Costco", "active": "1"}]
    assert match_supplier("Costco", pool)["id"] == 8


def test_product_exact_and_abbreviated_ticket_text():
    assert match_product("Detergente Líquido Ariel RevitaColor 8.5 L", PRODUCTS)["id"] == 10
    out = match_product("DET LIQ ARIEL REVITACOLOR 8500ML", PRODUCTS)
    assert (out["status"], out["id"]) == (MATCHED, 10)


def test_product_size_mismatch_is_not_a_match():
    out = match_product("Detergente Líquido Ariel RevitaColor 2 L", PRODUCTS)
    assert out["status"] == NEW and out["id"] is None


def test_product_superset_name_is_not_auto_matched():
    # "Coca Cola" is contained in "Coca Cola Light 600 ml" but is not the same product.
    assert match_product("Coca Cola", PRODUCTS)["status"] != MATCHED


def test_product_new():
    assert match_product("Papel higiénico 12 rollos", PRODUCTS)["status"] == NEW


def test_list_tools_filter_to_company(monkeypatch):
    from tools import backend_api
    monkeypatch.setattr(backend_api, "_post", lambda path, body: {"suppliers": [
        {"supplierId": 1, "companyId": 3, "supplierName": "A"},
        {"supplierId": 2, "companyId": "5", "supplierName": "B"},
        {"supplierId": 3, "companyId": 5, "supplierName": "C"},
    ]})
    monkeypatch.setattr(backend_api, "_get", lambda path: {"products": [
        {"productId": 1, "companyId": 5, "name": "x"}, {"productId": 2, "companyId": 9, "name": "y"},
    ]})
    assert [s["supplierId"] for s in backend_api.list_suppliers(5)] == [2, 3]
    assert [p["productId"] for p in backend_api.list_products(5)] == [1]


# ---- endpoint wiring ---------------------------------------------------

_FAKE_TICKET = {
    "isPurchaseTicket": True, "confidence": 0.9, "merchantName": "Sam's Club", "merchantRfc": "",
    "ticketNumber": "", "ticketDate": "2026-10-01", "currency": "MXN", "subtotal": 0.0, "tax": 0.0,
    "total": 935.95, "paymentType": "EFECTIVO", "paymentTypeRaw": "EFECTIVO", "cardLast4": "",
    "lineItems": [
        {"name": "Detergente Líquido Ariel RevitaColor 8.5 L", "quantity": 1, "unitPrice": 0, "lineTotal": 339.99, "needsReview": False},
        {"name": "Papel higiénico 12 rollos", "quantity": 1, "unitPrice": 0, "lineTotal": 595.96, "needsReview": False},
    ],
    "unreadableFields": [], "notes": [],
}


def _client(monkeypatch, suppliers=None, products=None, fail=False, ticket=None):
    from fastapi.testclient import TestClient
    import main

    class Ev:
        content = True
        def is_final_response(self): return True

    async def fake_run(user_id, session_id, new_message):
        yield Ev()

    async def fake_get(**kw):
        state = ticket if ticket is not None else json.dumps(_FAKE_TICKET)
        return types.SimpleNamespace(state={"ticket_extraction_result": state})

    def lookup(rows):
        def _f(company_id):
            if fail:
                raise RuntimeError("backend down")
            return rows
        return _f

    monkeypatch.setattr(main._ticket_extraction_runner, "run_async", fake_run)
    monkeypatch.setattr(main._session_service, "get_session", fake_get)
    monkeypatch.setattr(main, "list_suppliers", lookup(suppliers or SUPPLIERS))
    monkeypatch.setattr(main, "list_products", lookup(products or PRODUCTS))
    return TestClient(main.app)


def _post(client, **extra):
    body = {"ticketBase64": base64.b64encode(b"\x89PNG..").decode(), **extra}
    return client.post("/expenses/extract-ticket", json=body).json()


def test_endpoint_returns_matches_when_company_given(monkeypatch):
    out = _post(_client(monkeypatch), companyId=5)
    assert (out["supplierMatch"]["status"], out["supplierMatch"]["id"]) == (MATCHED, 1)
    assert [i["productMatch"]["status"] for i in out["lineItems"]] == [MATCHED, NEW]
    assert out["lineItems"][0]["productMatch"]["id"] == 10


def test_endpoint_skips_matching_without_company(monkeypatch):
    out = _post(_client(monkeypatch))
    assert out["supplierMatch"]["status"] == UNAVAILABLE
    assert all(i["productMatch"]["status"] == UNAVAILABLE for i in out["lineItems"])


def test_endpoint_survives_backend_lookup_failure(monkeypatch):
    out = _post(_client(monkeypatch, fail=True), companyId=5)
    assert out["total"] == 935.95 and len(out["lineItems"]) == 2
    assert out["supplierMatch"]["status"] == UNAVAILABLE
    assert all(i["productMatch"]["status"] == UNAVAILABLE for i in out["lineItems"])


def test_endpoint_handles_enum_members_in_adk_state(monkeypatch):
    # With output_schema, ADK leaves PaymentType enum members in state (found
    # in a live Gemini run); str() of one is "PaymentType.TARJETA_DEBITO".
    from agents.ticket_extraction.schema import PaymentType
    ticket = {**_FAKE_TICKET, "paymentType": PaymentType.TARJETA_DEBITO}
    out = _post(_client(monkeypatch, ticket=ticket))
    assert out["paymentType"] == "TARJETA_DEBITO"
    assert out["paymentMethod"] == "Tarjeta"
