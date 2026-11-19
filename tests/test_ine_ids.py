"""RFC-015: CIC/OCR from the INE back are kept only with the exact digit count.
Does not call Gemini."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.id_document.ine_ids import clean_ine_ids
from agents.id_document.prompt import INSTRUCTION


def test_valid_values_pass_through():
    assert clean_ine_ids("123456789", "1234567890123") == ("123456789", "1234567890123", [])


def test_spaces_and_fillers_are_stripped():
    assert clean_ine_ids("123 456 789", "1234<567890123") == ("123456789", "1234567890123", [])


def test_wrong_lengths_are_blanked_and_flagged():
    cic, ocr, dropped = clean_ine_ids("12345678", "12345678901234")
    assert (cic, ocr) == ("", "")
    assert dropped == ["cic", "ocr"]


def test_check_digit_included_by_mistake_is_rejected():
    # IDMEX + 9-digit CIC + 1 check digit: 10 digits must not be stored as a CIC.
    assert clean_ine_ids("1234567890", "")[0] == ""


def test_empty_stays_empty_and_unflagged():
    assert clean_ine_ids("", None) == ("", "", [])


def test_prompt_asks_for_both_fields_and_never_guessing():
    assert '"cic"' in INSTRUCTION and '"ocr"' in INSTRUCTION
    assert "IDMEX" in INSTRUCTION


from agents.id_document.ine_ids import clean_vigencia


def test_vigencia_takes_the_last_year_of_a_range():
    assert clean_vigencia("2023 - 2033") == ("2033", False)


def test_vigencia_single_year():
    assert clean_vigencia("2025") == ("2025", False)


def test_vigencia_out_of_range_is_dropped():
    assert clean_vigencia("1999") == ("", True)


def test_vigencia_unreadable_is_flagged_and_empty_is_not():
    assert clean_vigencia("VIGENCIA ??") == ("", True)
    assert clean_vigencia("") == ("", False)


def test_prompt_asks_for_vigencia():
    assert '"vigencia"' in INSTRUCTION and "VIGENCIA" in INSTRUCTION
