"""
Ticket pack breakdown (matching.parse_pack) and the web lookup's code-side checks
(product_web_lookup.reconcile) + endpoint wiring with the agent stubbed. No Gemini,
no backend.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.ticket_extraction.matching import parse_pack
from agents.ticket_extraction.proposal import build_proposal
from agents.product_web_lookup.reconcile import normalize_unit, reconcile_web_lookup, suggested_name


# ---- parse_pack ------------------------------------------------------------
@pytest.mark.parametrize("text, units, content, unit", [
    ("45/500ML AGU", 45, 500, "ml"),
    ("MEMBERS MARK 45/500 ML", 45, 500, "ml"),
    ("COCA COLA 12X355ML", 12, 355, "ml"),
    ("8L ARIEL REG", 1, 8, "L"),
    ("10K 123 JAZM", 1, 10, "kg"),
    ("SUAVITEL 8.5L", 1, 8.5, "L"),
    ("DET 1,5 KG", 1, 1.5, "kg"),
    ("AGUA C/24 600ML", 24, 600, "ml"),
    ("SERVILLETAS 24 PZAS", 24, 0, ""),
    ("AXION ROSAS", 1, 0, ""),
    ("", 1, 0, ""),
])
def test_parse_pack(text, units, content, unit):
    assert parse_pack(text) == {"unitsPerPack": units, "netContent": content, "unitOfMeasure": unit}


def test_proposal_lines_carry_the_breakdown_and_stay_per_pack():
    p = build_proposal({
        "merchantName": "SAMS", "supplierMatch": {"status": "NEW"}, "total": 122.76,
        "lineItems": [{"code": "981017658", "name": "45/500ML AGU", "quantity": 1, "unitPrice": 0,
                       "lineTotal": 122.76, "discount": 0, "productMatch": {"status": "NEW"}}],
    })
    line = p["lines"][0]
    assert (line["unitsPerPack"], line["netContent"], line["unitOfMeasure"]) == (45, 500, "ml")
    assert (line["quantity"], line["unitCost"]) == (1, 122.76)  # converted to pieces by the app on save


# ---- reconcile_web_lookup --------------------------------------------------
WATER = {"found": True, "fullName": "Agua Purificada Member's Mark 45 pzas de 500 ml", "brand": "Member's Mark",
         "productName": "Agua Purificada", "variant": "", "description": "Agua purificada.",
         "netContent": 500, "unitOfMeasure": "ml", "unitsPerPack": 45, "price": 122.76,
         "codeConfirmed": True, "notes": []}
SOURCES = [{"title": "sams.com.mx", "url": "https://example.invalid/r"}]
TICKET = {"unitsPerPack": 45, "netContent": 500.0, "unitOfMeasure": "ml"}


def test_confirmed_code_with_sources_is_verified():
    r = reconcile_web_lookup(WATER, SOURCES, TICKET)
    assert r["verified"] and not r["needsReview"] and r["issues"] == []
    assert r["suggestedName"] == "Member's Mark Agua Purificada 500 ml"


def test_a_confirmation_without_sources_is_not_trusted():
    r = reconcile_web_lookup(WATER, [], TICKET)
    assert not r["verified"] and r["needsReview"]
    assert any("fuentes" in i for i in r["issues"])


def test_unconfirmed_code_is_flagged():
    r = reconcile_web_lookup({**WATER, "codeConfirmed": False}, SOURCES, TICKET)
    assert not r["verified"] and any("código exacto" in i for i in r["issues"])


def test_disagreement_with_the_ticket_is_flagged():
    r = reconcile_web_lookup({**WATER, "unitsPerPack": 24, "netContent": 600}, SOURCES, TICKET)
    assert any("45 piezas" in i for i in r["issues"]) and any("500 ml" in i for i in r["issues"])


def test_same_size_in_other_units_is_not_a_disagreement():
    r = reconcile_web_lookup({**WATER, "unitsPerPack": 1, "netContent": 10, "unitOfMeasure": "Kilogramos"},
                             SOURCES, {"unitsPerPack": 1, "netContent": 10000, "unitOfMeasure": "g"})
    assert r["unitOfMeasure"] == "kg" and r["issues"] == []


def test_not_found_gives_no_suggestion():
    r = reconcile_web_lookup({"found": False, "notes": []}, SOURCES, TICKET)
    assert r == {**r, "found": False, "verified": False, "suggestedName": "", "needsReview": True}
    assert r["issues"] == ["No se encontró el producto en internet."]


def test_malformed_reply_is_normalized():
    r = reconcile_web_lookup({"found": True, "fullName": "X", "unitOfMeasure": "galones", "netContent": 3,
                              "unitsPerPack": 0, "extra": 1}, SOURCES)
    assert (r["unitOfMeasure"], r["netContent"], r["unitsPerPack"]) == ("", 0.0, 1)


def test_loose_types_from_the_model_fall_back_instead_of_failing():
    r = reconcile_web_lookup({"found": "true", "fullName": "Ariel 8 L", "netContent": "8 L", "unitOfMeasure": "L",
                              "unitsPerPack": None, "price": None, "codeConfirmed": "false", "notes": ""}, SOURCES)
    assert (r["found"], r["netContent"], r["unitsPerPack"], r["price"], r["codeConfirmed"]) == (True, 8.0, 1, 0.0, False)


def test_suggested_name_does_not_repeat_the_size():
    assert suggested_name({"brand": "Axion", "productName": "Lavatrastes 2.8 L", "variant": "",
                           "fullName": "", "netContent": 2.8, "unitOfMeasure": "L"}) == "Axion Lavatrastes 2.8 L"
    assert normalize_unit("Lts.") == "L"


# ---- endpoint wiring -------------------------------------------------------
def client(monkeypatch, reply: str, sources=SOURCES, products=None):
    from fastapi.testclient import TestClient
    import main

    async def fake_run(**_kwargs):
        chunks = [SimpleNamespace(web=SimpleNamespace(uri=s["url"], title=s["title"])) for s in sources]
        yield SimpleNamespace(
            grounding_metadata=SimpleNamespace(grounding_chunks=chunks),
            content=SimpleNamespace(parts=[SimpleNamespace(text=reply, thought=False)]),
            is_final_response=lambda: True,
        )

    monkeypatch.setattr(main._product_web_lookup_runner, "run_async", fake_run)
    monkeypatch.setattr(main, "list_products", lambda _cid: products or [])
    return TestClient(main.app)


def test_endpoint_returns_the_checked_result(monkeypatch):
    import json
    c = client(monkeypatch, "```json\n" + json.dumps(WATER) + "\n```")
    body = c.post("/products/web-lookup", json={"merchantName": "Sam's Club", "code": "981017658",
                                                 "ticketName": "45/500ML AGU"}).json()
    assert body["verified"] and body["unitsPerPack"] == 45 and body["sources"][0]["title"] == "sams.com.mx"


def test_endpoint_flags_an_existing_catalog_product(monkeypatch):
    import json
    c = client(monkeypatch, json.dumps(WATER),
               products=[{"productId": 9, "name": "Agua", "barCode": "981017658", "companyId": 1}])
    body = c.post("/products/web-lookup", json={"code": "981017658", "ticketName": "45/500ML AGU",
                                                 "companyId": 1}).json()
    assert body["existingProduct"]["id"] == 9 and body["needsReview"]


def test_endpoint_survives_a_non_json_reply(monkeypatch):
    body = client(monkeypatch, "lo siento", sources=[]).post(
        "/products/web-lookup", json={"code": "1", "ticketName": "X"}).json()
    assert body["found"] is False and body["needsReview"]


def test_endpoint_requires_code_or_name(monkeypatch):
    assert client(monkeypatch, "{}").post("/products/web-lookup", json={}).status_code == 400
