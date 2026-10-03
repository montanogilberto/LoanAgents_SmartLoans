"""
Ticket proposal + verdict — the decision logic that used to live in the POS app
(ticketDraft.ts / agentExpense.ts), now owned by this service. Pure code: no
Gemini, no backend. Cases are ported 1:1 from the TypeScript tests they replace.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.ticket_extraction.proposal import build_proposal, evaluate, unit_cost


def match(status, id_=None, name="", candidates=None):
    return {"status": status, "id": id_, "name": name, "score": 0.9, "candidates": candidates or []}


def line(name="Detergente 8.5 L", id_=10, qty=1, total=339.99, unit_price=0.0, status="MATCHED", **over):
    return {"name": name, "quantity": qty, "unitPrice": unit_price, "lineTotal": total, "needsReview": False,
            "productMatch": match(status, id_ if status == "MATCHED" else None, name if status == "MATCHED" else ""), **over}


def clear(**over):
    base = {
        "isPurchaseTicket": True, "confidence": 0.92, "merchantName": "Sam's Club", "ticketDate": "2026-10-01",
        "total": 769.95, "paymentMethod": "Tarjeta", "supplierMatch": match("MATCHED", 7, "SAMS CLUB MEXICO"),
        "lineItems": [line("Detergente 8.5 L", 10, 1, 339.99), line("Suavizante 8.5 L", 12, 2, 429.96)],
        "itemsTotal": 769.95, "totalMatchesItems": True, "issues": [], "needsReview": False,
    }
    base.update(over)
    return base


# ── unit cost ────────────────────────────────────────────────────────────────
def test_unit_cost_prefers_the_printed_unit_price():
    assert unit_cost(line(qty=2, unit_price=214.98, total=429.96)) == 214.98


def test_unit_cost_is_derived_from_the_line_total_when_not_printed():
    assert unit_cost(line(qty=2, total=429.96)) == 214.98
    assert unit_cost(line(qty=3, total=100)) == 33.33


def test_unit_cost_does_not_divide_by_zero_quantity():
    assert unit_cost(line(qty=0, total=50)) == 50


# ── proposal ─────────────────────────────────────────────────────────────────
def test_matched_supplier_and_lines_are_proposed_as_existing_records():
    p = build_proposal(clear())
    assert p["supplier"] == {"action": "use", "supplierId": 7, "name": "SAMS CLUB MEXICO", "candidates": []}
    assert [(l["action"], l["productId"], l["quantity"], l["unitCost"]) for l in p["lines"]] == [
        ("use", 10, 1.0, 339.99), ("use", 12, 2.0, 214.98)]
    assert p["paymentMethod"] == "Tarjeta" and p["paymentDate"] == "2026-10-01" and p["ticketTotal"] == 769.95


def test_new_merchant_and_new_products_are_proposed_for_creation():
    p = build_proposal(clear(
        supplierMatch=match("NEW"),
        lineItems=[line("Jabón Member's Mark 5 L", status="NEW"), line("Detergente", 10)]))
    assert p["supplier"]["action"] == "create" and p["supplier"]["name"] == "Sam's Club"
    assert [l["action"] for l in p["lines"]] == ["create", "use"]
    assert p["lines"][0]["productId"] is None and p["lines"][0]["name"] == "Jabón Member's Mark 5 L"


def test_ambiguous_or_unavailable_matches_are_never_auto_selected():
    cands = [{"id": 1, "name": "A", "score": 0.8}, {"id": 2, "name": "B", "score": 0.79}]
    p = build_proposal(clear(
        supplierMatch=match("AMBIGUOUS", candidates=cands),
        lineItems=[line(status="AMBIGUOUS"), line(status="UNAVAILABLE")]))
    assert p["supplier"]["action"] == "choose" and p["supplier"]["supplierId"] is None
    assert p["supplier"]["candidates"] == cands
    assert [l["action"] for l in p["lines"]] == ["choose", "choose"]


def test_new_supplier_without_a_readable_name_must_be_chosen_not_created_blank():
    p = build_proposal(clear(supplierMatch=match("NEW"), merchantName="  "))
    assert p["supplier"]["action"] == "choose"


def test_blank_payment_and_date_stay_blank_and_total_zero_when_unreadable():
    p = build_proposal(clear(paymentMethod="", ticketDate="", total=0))
    assert (p["paymentMethod"], p["paymentDate"], p["ticketTotal"]) == ("", "", 0.0)


def test_a_zero_quantity_line_counts_as_one_unit():
    assert build_proposal(clear(lineItems=[line(qty=0, total=50)]))["lines"][0]["quantity"] == 1.0


# ── verdict ──────────────────────────────────────────────────────────────────
def test_ready_when_everything_was_read_and_matched():
    assert evaluate(clear()) == {"ready": True, "reasons": []}


def test_a_non_ticket_image_is_rejected_on_its_own():
    assert evaluate(clear(isPurchaseTicket=False))["reasons"] == ["La imagen no parece un ticket de compra."]


@pytest.mark.parametrize("name,over,fragment", [
    ("ambiguous supplier", {"supplierMatch": match("AMBIGUOUS")}, "varios proveedores"),
    ("new supplier", {"supplierMatch": match("NEW")}, "no está registrado"),
    ("unavailable supplier", {"supplierMatch": match("UNAVAILABLE")}, "identificar el proveedor"),
    ("no date", {"ticketDate": ""}, "fecha"),
    ("no payment method", {"paymentMethod": ""}, "método de pago"),
    ("no total", {"total": 0}, "total"),
    ("no lines", {"lineItems": []}, "productos"),
    ("low confidence", {"confidence": 0.4}, "poca confianza"),
    ("agent flagged", {"needsReview": True}, "por revisar"),
    ("issues present", {"issues": ["x"]}, "por revisar"),
    ("lines do not add up", {"totalMatchesItems": False}, "no suman"),
    ("unknown reconciliation", {"totalMatchesItems": None}, "no suman"),
])
def test_not_ready(name, over, fragment):
    v = evaluate(clear(**over))
    assert v["ready"] is False
    assert any(fragment in r for r in v["reasons"]), (name, v["reasons"])


def test_counts_products_not_matched_to_the_catalog():
    lines = [line("a", 1, 1, 10), line("b", qty=1, total=10, status="NEW"), line("c", qty=1, total=10, status="AMBIGUOUS")]
    v = evaluate(clear(lineItems=lines, total=30, itemsTotal=30))
    assert "2 productos no están en el catálogo o hay dudas." in v["reasons"]


def test_saved_total_is_guarded_against_the_ticket_total():
    # 3 × 33.33 = 99.99 saved vs 100.00 on the ticket: within the one-peso tolerance, still ready
    assert evaluate(clear(total=100, lineItems=[line("x", 1, 3, 100)]))["ready"] is True
    # lines silently far from the total: the agent's own flag is not trusted blindly
    odd = clear(total=500, lineItems=[line("x", 1, 1, 100, unit_price=300)])
    assert evaluate(odd)["ready"] is False


# ── wired into the real response builder ─────────────────────────────────────
def test_extract_ticket_response_carries_proposal_and_verdict():
    import main
    result = {
        "isPurchaseTicket": True, "confidence": 0.9, "merchantName": "Sam's Club", "total": 935.95,
        "paymentType": "TARJETA", "lineItems": [
            {"name": "Detergente", "quantity": 1, "unitPrice": 0, "lineTotal": 339.99},
            {"name": "Suavizante", "quantity": 2, "unitPrice": 0, "lineTotal": 429.96},
        ],
    }
    resp = main._build_ticket_response(result, main.RecordMatch(status="NEW"),
                                       [main.RecordMatch(status="NEW"), main.RecordMatch(status="MATCHED", id=12, name="Suavizante Suavitel")])
    assert resp.proposal.supplier.action == "create"
    assert [l.action for l in resp.proposal.lines] == ["create", "use"]
    assert resp.proposal.lines[1].unitCost == 214.98
    assert resp.verdict.ready is False and any("no está registrado" in r for r in resp.verdict.reasons)
    # a response with nothing usable still serializes with a proposal (never crashes)
    assert main._build_ticket_response({}).proposal is not None
