"""Clave de elector layout validation for the INE extraction. Does not call Gemini."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.id_document.clave_elector import clean_clave_elector
from agents.id_document.curp import check_digit

# Made-up but well-formed: name letters, born 1990-10-16, Sonora (26), H, homoclave 000.
VALID = "MNQHGL90101626H000"


def curp_for(birth: str, sex: str) -> str:
    c17 = f"MOQG{birth}{sex}SRNLB0"
    return c17 + str(check_digit(c17))


def test_valid_clave_passes_through_normalized():
    assert clean_clave_elector(" mnqhgl 901016-26h000 ") == (VALID, [])


def test_wrong_length_is_blanked_and_flagged():
    # The 19-character reading Gemini returned for a blurry card.
    assert clean_clave_elector("MNQHGU901016H296000") == ("", ["claveElector"])
    assert clean_clave_elector(VALID[:17]) == ("", ["claveElector"])


def test_bad_layout_is_blanked_and_flagged():
    assert clean_clave_elector("MNQHG790101626H000") == ("", ["claveElector"])  # digit among the letters
    assert clean_clave_elector("MNQHGL90131626H000") == ("", ["claveElector"])  # month 13
    assert clean_clave_elector("MNQHGL90101699H000") == ("", ["claveElector"])  # state 99
    assert clean_clave_elector("MNQHGL90101626Q000") == ("", ["claveElector"])  # sex not H/M


def test_letter_digit_swaps_in_fixed_positions_are_fixed():
    assert clean_clave_elector("MNQHGL9O1O1626H0O0") == (VALID, [])


def test_empty_is_not_flagged():
    assert clean_clave_elector("") == ("", [])


def test_birth_date_mismatch_keeps_both_and_flags_them():
    assert clean_clave_elector(VALID, "16/10/1990") == (VALID, [])
    assert clean_clave_elector(VALID, "19/05/1986") == (VALID, ["claveElector", "fechaNacimiento"])


def test_disagreement_with_a_valid_curp_is_flagged():
    assert clean_clave_elector(VALID, curp=curp_for("901016", "H")) == (VALID, [])
    assert clean_clave_elector(VALID, curp=curp_for("860519", "H")) == (VALID, ["claveElector"])
    assert clean_clave_elector(VALID, curp=curp_for("901016", "M")) == (VALID, ["claveElector"])
