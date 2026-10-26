"""
Tests for the ticket extraction agent -- wiring smoke tests plus the
deterministic reconcile checks. Does not call Gemini or make HTTP requests.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.ticket_extraction.reconcile import date_from_timestamp, normalize_line_items, payment_label, reconcile

NOW = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)


def _item(name, qty, line, unit=0.0, review=False):
    return {"name": name, "quantity": qty, "unitPrice": unit, "lineTotal": line, "needsReview": review}


def _ticket(**overrides):
    base = {
        "isPurchaseTicket": True, "ticketDate": "2026-10-01", "subtotal": 0.0, "tax": 0.0,
        "total": 935.95, "unreadableFields": [],
        "lineItems": [
            _item("Detergente Líquido Ariel RevitaColor 8.5 L", 1, 339.99),
            _item("Jabón Líquido para Manos Member's Mark Antibacterial Aroma Mandarina 5 L", 1, 166.00),
            _item("Suavizante de Telas Suavitel Cuidado Diario Fresca Primavera 8.5 L", 2, 429.96),
        ],
    }
    base.update(overrides)
    return base


def test_agent_importable_and_wired():
    from agents.ticket_extraction import ticket_extraction_agent
    from agents.ticket_extraction.schema import TicketExtractionResult
    assert ticket_extraction_agent.name == "ticket_extraction_agent"
    assert ticket_extraction_agent.output_key == "ticket_extraction_result"
    assert ticket_extraction_agent.output_schema is TicketExtractionResult
    assert not ticket_extraction_agent.tools


def test_prompt_exports_instruction():
    from agents.ticket_extraction.prompt import INSTRUCTION
    assert isinstance(INSTRUCTION, str) and "paymentType" in INSTRUCTION


def test_consistent_ticket_has_no_issues():
    out = reconcile(_ticket(), now=NOW)
    assert out["issues"] == []
    assert out["itemsTotal"] == 935.95
    assert out["totalMatchesItems"] is True
    assert out["needsReview"] is False


def test_cropped_online_order_is_flagged():
    # The Sam's Club screenshot: only 3 of the lines are visible but the
    # "Cargo original" is $1,701.93.
    out = reconcile(_ticket(total=1701.93), now=NOW)
    assert out["totalMatchesItems"] is False
    assert out["needsReview"] is True
    assert any("1,701.93" in i for i in out["issues"])


def test_total_matches_subtotal_or_total_minus_tax():
    assert reconcile(_ticket(total=1085.69, subtotal=935.95, tax=149.74), now=NOW)["totalMatchesItems"] is True
    assert reconcile(_ticket(total=1085.69, tax=149.74), now=NOW)["totalMatchesItems"] is True


def test_line_math_mismatch_is_flagged():
    out = reconcile(_ticket(lineItems=[_item("Cable", 2, 100.0, unit=20.0)], total=100.0), now=NOW)
    assert any("Cable" in i for i in out["issues"])


def test_future_or_bad_date_is_blanked():
    assert reconcile(_ticket(ticketDate="2027-01-01"), now=NOW)["ticketDate"] == ""
    assert reconcile(_ticket(ticketDate="12/02/2026"), now=NOW)["ticketDate"] == ""
    assert reconcile(_ticket(ticketDate="2026-10-02"), now=NOW)["ticketDate"] == "2026-10-02"


def test_unreadable_total_and_fields_reported():
    out = reconcile(_ticket(total=0.0, lineItems=[], unreadableFields=["merchantName"]), now=NOW)
    assert out["needsReview"] is True
    assert any("total" in i.lower() for i in out["issues"])
    assert any("merchantName" in i for i in out["issues"])


def test_non_ticket_needs_review():
    assert reconcile({"isPurchaseTicket": False}, now=NOW)["needsReview"] is True


def test_payment_labels():
    assert payment_label("EFECTIVO") == "Efectivo"
    assert payment_label("TARJETA_CREDITO") == "Tarjeta"
    assert payment_label("TARJETA_DEBITO") == "Tarjeta"
    assert payment_label("TRANSFERENCIA") == "Transferencia"
    assert payment_label("CHEQUE") == ""   # not an option on the form
    assert payment_label("NO_VISIBLE") == ""
    assert payment_label("garbage") == ""


# ── Sam's Club ticket (2026-10-22): item codes, "Promocion Especial", TS stamp ──
def _sams(**overrides):
    return _ticket(**{"ticketDate": "", "timestampRaw": "TS:221026153017", "subtotal": 1233.73, "total": 1233.73, "tax": 153.24, **overrides}, lineItems=[
        {"code": "", "name": "84281 SUAVITEL", "quantity": 2, "unitPrice": 219.94, "lineTotal": 439.88},
        _item("981055673 AXION ROSAS", 1, 121.74),
        _item("980027611 8L ARIEL REG", 1, 347.82),
        _item("Promocion Especial", 1, -42.97),
        _item("981017658 45/500ML AGU", 1, 122.76),
        _item("980011358 10K 123 JAZM", 1, 289.51),
        _item("Promocion Especial", 1, -45.01),
    ])


def test_promotions_fold_into_the_item_above_and_codes_split_out():
    result = _sams()
    items = normalize_line_items(result)
    assert [(i["code"], i["name"], i["discount"]) for i in items] == [
        ("84281", "SUAVITEL", 0.0),
        ("981055673", "AXION ROSAS", 0.0),
        ("980027611", "8L ARIEL REG", 42.97),
        ("981017658", "45/500ML AGU", 0.0),
        ("980011358", "10K 123 JAZM", 45.01),
    ]
    assert normalize_line_items(result) == items  # idempotent
    out = reconcile(result, now=datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc))
    assert out["itemsTotal"] == 1233.73 and out["totalMatchesItems"] is True


def test_promotion_given_as_positive_amount_is_still_a_discount():
    result = _ticket(lineItems=[_item("Detergente", 1, 347.82), _item("Promocion Especial", 1, 42.97)], total=304.85)
    assert [(i["name"], i["discount"]) for i in normalize_line_items(result)] == [("Detergente", 42.97)]


def test_ticket_date_comes_from_the_ts_stamp_when_none_is_printed():
    now = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)
    assert date_from_timestamp("TS:221026153017", now) == "2026-10-22"   # DDMMYY beats 2022-10-26
    assert date_from_timestamp("TS:261022091500", now) == "2026-10-22"   # YYMMDD store
    assert date_from_timestamp("", now) == "" and date_from_timestamp("TS:12", now) == ""
    out = reconcile(_sams(), now=now)
    assert out["ticketDate"] == "2026-10-22"
    assert any("sello" in i for i in out["issues"])
    # a printed date always wins over the stamp
    assert reconcile(_sams(ticketDate="2026-10-21"), now=now)["ticketDate"] == "2026-10-21"
