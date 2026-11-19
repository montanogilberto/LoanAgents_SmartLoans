"""CURP check-digit validation for the INE extraction. Does not call Gemini."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.id_document.curp import check_digit, clean_curp, is_valid_curp

# RENAPO's published sample CURP.
VALID = "MAHJ280603MSPRRV09"


def with_digit(curp17: str) -> str:
    return curp17 + str(check_digit(curp17))


def test_known_sample_is_valid():
    assert is_valid_curp(VALID)


def test_valid_curp_passes_through_normalized():
    assert clean_curp(" mahj 280603-msprrv09 ") == (VALID, [])


def test_wrong_check_digit_is_blanked_and_flagged():
    assert clean_curp(VALID[:17] + "8") == ("", ["curp"])


def test_wrong_length_is_blanked_and_flagged():
    assert clean_curp(VALID[:17]) == ("", ["curp"])
    assert clean_curp(VALID + "X") == ("", ["curp"])


def test_empty_is_not_flagged():
    assert clean_curp("") == ("", [])


def test_letter_digit_swaps_are_fixed_only_when_the_check_digit_confirms():
    curp = with_digit("MOQG860519HSRNLB0")
    misread = curp[:4] + "86O519" + curp[10:]  # O read for 0 in the birth date
    assert clean_curp(misread) == (curp, [])
    misread_letter = "M0QG" + curp[4:]  # 0 read for O in the name letters
    assert clean_curp(misread_letter) == (curp, [])


def test_birth_date_mismatch_keeps_both_and_flags_them():
    curp = with_digit("MOQG860519HSRNLB0")
    assert clean_curp(curp, "19/05/1986") == (curp, [])
    assert clean_curp(curp, "20/08/1975") == (curp, ["curp", "fechaNacimiento"])


def test_never_invents_a_curp_from_garbage():
    assert clean_curp("NO DETECTADO") == ("", ["curp"])
