"""
Tests for the product label agent -- wiring smoke tests, the deterministic
reconcile checks and the duplicate lookup. Does not call Gemini or make HTTP
requests. Fixture values come from a real photo (Electrolit Manzana 1000 mL,
barcode 7 501125 104350) used to design the agent on 2026-10-11.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.product_extraction.reconcile import gtin_is_valid, reconcile, suggested_name

TODAY = date(2026, 10, 11)


def _label(**overrides):
    base = {
        "isProduct": True, "confidence": 0.92,
        "brand": "Electrolit", "productName": "Suero Rehidratante", "variant": "Manzana",
        "description": "Solución esterilizada de electrolitos orales indicada para prevenir o tratar la deshidratación.",
        "barCode": "7501125104350",
        "netContent": 1000.0, "unitOfMeasure": "ml", "netContentRaw": "Frasco con 1000 mL",
        "expirationDate": "", "expirationRaw": "", "lotNumber": "",
        "manufacturer": "Laboratorios PiSA, S.A. de C.V.",
        "unreadableFields": [], "notes": [],
    }
    base.update(overrides)
    return base


# ── wiring ───────────────────────────────────────────────────────────────────

def test_agent_importable_and_wired():
    from agents.product_extraction import product_extraction_agent
    from agents.product_extraction.schema import ProductLabelResult
    assert product_extraction_agent.name == "product_extraction_agent"
    assert product_extraction_agent.output_key == "product_extraction_result"
    assert product_extraction_agent.output_schema is ProductLabelResult
    assert not product_extraction_agent.tools


def test_schema_units_match_the_pos_form():
    from agents.product_extraction.schema import UnitOfMeasure
    assert {u.value for u in UnitOfMeasure} - {"NO_VISIBLE"} == {"g", "kg", "ml", "L", "pz"}


def test_prompt_exports_instruction():
    from agents.product_extraction.prompt import INSTRUCTION
    assert isinstance(INSTRUCTION, str) and "barCode" in INSTRUCTION and "never guess" in INSTRUCTION


# ── barcode ──────────────────────────────────────────────────────────────────

def test_real_ean13_is_valid():
    assert gtin_is_valid("7501125104350")          # the Electrolit bottle
    assert gtin_is_valid("7501234567893")


def test_one_misread_digit_fails_the_check_digit():
    assert not gtin_is_valid("7501125104351")
    assert not gtin_is_valid("7501125104850")


def test_barcode_with_spaces_is_normalized():
    assert reconcile(_label(barCode="7 501125 104350"), today=TODAY)["barCode"] == "7501125104350"


def test_bad_barcode_is_blanked_and_flagged():
    out = reconcile(_label(barCode="7501125104351"), today=TODAY)
    assert out["barCode"] == ""
    assert out["needsReview"] is True
    assert any("dígito verificador" in i for i in out["issues"])


def test_short_barcode_is_blanked_and_flagged():
    out = reconcile(_label(barCode="75011"), today=TODAY)
    assert out["barCode"] == "" and any("longitud" in i for i in out["issues"])


def test_missing_barcode_is_not_an_issue():
    out = reconcile(_label(barCode=""), today=TODAY)
    assert out["barCode"] == "" and out["issues"] == []


# ── net content ──────────────────────────────────────────────────────────────

def test_clean_label_has_no_issues_and_a_full_name():
    out = reconcile(_label(), today=TODAY)
    assert out["issues"] == []
    assert out["needsReview"] is False
    assert out["suggestedName"] == "Electrolit Suero Rehidratante Manzana 1000 ml"
    assert (out["netContent"], out["unitOfMeasure"]) == (1000.0, "ml")


def test_no_net_content_clears_the_unit():
    out = reconcile(_label(netContent=0.0, unitOfMeasure="NO_VISIBLE"), today=TODAY)
    assert (out["netContent"], out["unitOfMeasure"]) == (0.0, "")
    assert out["suggestedName"] == "Electrolit Suero Rehidratante Manzana"
    assert out["issues"] == []


def test_unknown_unit_is_flagged():
    out = reconcile(_label(unitOfMeasure="oz"), today=TODAY)
    assert out["unitOfMeasure"] == "" and any("unidad" in i for i in out["issues"])


# ── expiration ───────────────────────────────────────────────────────────────

def test_future_expiration_is_kept():
    out = reconcile(_label(expirationDate="2027-05-31"), today=TODAY)
    assert out["expirationDate"] == "2027-05-31" and out["issues"] == []


def test_expired_product_is_flagged_but_kept():
    out = reconcile(_label(expirationDate="2026-01-31"), today=TODAY)
    assert out["expirationDate"] == "2026-01-31"
    assert any("CADUCADO" in i for i in out["issues"]) and out["needsReview"] is True


def test_non_iso_expiration_is_blanked():
    out = reconcile(_label(expirationDate="31/05/2027"), today=TODAY)
    assert out["expirationDate"] == "" and out["needsReview"] is True


# ── review flags ─────────────────────────────────────────────────────────────

def test_unreadable_field_is_reported():
    out = reconcile(_label(unreadableFields=["expirationDate"]), today=TODAY)
    assert any("expirationDate" in i for i in out["issues"]) and out["needsReview"] is True


def test_not_a_product_needs_review():
    out = reconcile(_label(isProduct=False), today=TODAY)
    assert out["needsReview"] is True


def test_low_confidence_needs_review_without_issues():
    out = reconcile(_label(confidence=0.4), today=TODAY)
    assert out["issues"] == [] and out["needsReview"] is True


def test_suggested_name_does_not_repeat_the_brand():
    assert suggested_name("Ariel", "Ariel Detergente Líquido", "RevitaColor", 8.5, "L") == \
        "Ariel Detergente Líquido RevitaColor 8.5 L"


# ── duplicate lookup ─────────────────────────────────────────────────────────

def test_existing_product_by_barcode_wins():
    from main import _find_existing_product
    products = [{"productId": 7, "name": "Suero Electrolit Manzana", "barCode": "7501125104350"},
                {"productId": 9, "name": "Garrafón 20 L", "barCode": ""}]
    match, by = _find_existing_product(reconcile(_label(), today=TODAY), products)
    assert (match.status, match.id, by) == ("MATCHED", 7, "barCode")


def test_no_barcode_falls_back_to_name():
    from main import _find_existing_product
    products = [{"productId": 7, "name": "Electrolit Suero Rehidratante Manzana 1000 ml", "barCode": ""}]
    match, by = _find_existing_product(reconcile(_label(barCode=""), today=TODAY), products)
    assert match.status == "MATCHED" and match.id == 7 and by == "name"


def test_unrelated_catalog_is_new():
    from main import _find_existing_product
    products = [{"productId": 9, "name": "Garrafón 20 L", "barCode": "7500000000007"}]
    match, by = _find_existing_product(reconcile(_label(), today=TODAY), products)
    assert match.status == "NEW" and by == ""
