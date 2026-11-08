"""Checks on the transfer receipt reading. Does not call Gemini."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.transfer_extraction.reconcile import review_extraction

TODAY = date(2026, 11, 6)
GOOD = {"isTransferReceipt": True, "confidence": 0.95, "amountMXN": 500, "claveRastreo": "mbanB-123 456",
        "transferDate": "2026-11-05", "bankFrom": "BBVA", "issues": []}


def test_a_clean_receipt_needs_no_review():
    r = review_extraction(GOOD, TODAY)
    assert r["claveRastreo"] == "MBANB123456"
    assert r["needsReview"] is False


def test_a_future_date_is_cleared():
    r = review_extraction({**GOOD, "transferDate": "2026-11-07"}, TODAY)
    assert r["transferDate"] == "" and r["needsReview"] is True


def test_a_bad_clave_is_cleared_not_kept():
    assert review_extraction({**GOOD, "claveRastreo": "12"}, TODAY)["claveRastreo"] == ""


def test_low_confidence_needs_review():
    assert review_extraction({**GOOD, "confidence": 0.4}, TODAY)["needsReview"] is True


def test_not_a_receipt_needs_review():
    assert review_extraction({**GOOD, "isTransferReceipt": False}, TODAY)["needsReview"] is True


def test_an_empty_result_is_safe():
    r = review_extraction({}, TODAY)
    assert r["amountMXN"] == 0 and r["needsReview"] is True
