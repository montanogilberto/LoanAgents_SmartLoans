"""Aval (guarantor) calls: what code decides, so the model doesn't. No network, no model."""
from __future__ import annotations

import difflib
import re
import unicodedata

ACCEPTS, DECLINES, UNSURE, DOES_NOT_KNOW = "ACCEPTS", "DECLINES", "UNSURE", "DOES_NOT_KNOW_APPLICANT"

_NOT_SPEECH = re.compile(r"<[^>]*>")


def words(text: str | None) -> list[str]:
    text = unicodedata.normalize("NFD", _NOT_SPEECH.sub(" ", (text or "").lower()))
    return re.findall(r"[a-z]+", "".join(c for c in text if not unicodedata.combining(c)))


def says_full_name(heard: str, full_name: str | None) -> bool:
    """First name AND a surname. The surname is never told to the model before this,
    so it can't be fed to whoever answers: a "sí" alone isn't enough."""
    expected, said = words(full_name), words(heard)
    if len(expected) < 2 or re.search(r"\bno soy\b|equivocad|no vive|no se encuentra|no esta\b(?! bien)",
                                      " ".join(said)):
        return False

    def has(word):
        return any(difflib.SequenceMatcher(None, word, w).ratio() >= 0.8 for w in said)
    return has(expected[0]) and any(has(w) for w in expected[1:] if len(w) > 3)


_YES = re.compile(r"\b(si|claro|correcto|acepto|de acuerdo|adelante|por supuesto|entiendo|entendido|esta bien|"
                  r"exacto|asi es|desde luego|ok|okey|okay|va|sale|perfecto)\b")
# A yes with any of these isn't a clear yes: a person reviews it.
_NOT_YES = re.compile(r"\b(no|pero|depende|quiza|quizas|tal vez|creo|supongo|todavia|aun|pensar\w*|duda|dudas|"
                      r"obligad[oa]|obligan|forzad[oa]|presion\w*|amenaz\w*|miedo)\b")


def clear_yes(heard: str) -> bool:
    said = " ".join(words(heard))
    return bool(_YES.search(said)) and not _NOT_YES.search(said)


_STOP = re.compile(r"\b(no|dejen? de|ya no|nunca|basta de)\s+(?:(?:me|quiero que|vuelvan?|vuelva|a)\s+)*"
                   r"(?:(?:llam|contact|molest|marc|busc)(?:e|en|ar)|marquen?|busquen?)\b")


def asks_to_stop(heard: str) -> bool:
    return bool(_STOP.search(" ".join(words(heard))))


_WRONG_NUMBER = re.compile(r"numero equivocado|se equivoc|\bno soy (esa|ese|la|el|quien)\b|no conozco a nadie")


def says_wrong_number(heard: str) -> bool:
    return bool(_WRONG_NUMBER.search(" ".join(words(heard))))


# The options of the app's Aval step (CreditProfileWizardRules.RELATIONSHIPS).
_RELATIONSHIPS = {
    "familiar": r"\b(herman|mama\b|papa\b|padre|madre|hij[oa]|espos|marido|mujer\b|prim[oa]|ti[oa]s?\b|abuel|"
                r"cunad|sobrin|suegr|yerno|nuera|famili|padrastro|madrastra|pareja)",
    "amistad": r"\b(amig|amist|compadre|comadre|conocid)",
    "trabajo": r"\b(trabaj|jef[ea]|coleg|companer|patron|emplead|soci[oa]|oficina|laboral)",
    "vecindad": r"\b(vecin)",
}


def relationship_check(declared: str | None, heard: str) -> str:
    """MATCH | DIFFERENT | UNCLEAR | NOT_COMPARED. The model never sees `declared`."""
    if (declared or "").strip().lower() not in _RELATIONSHIPS:
        return "NOT_COMPARED"
    said = " ".join(words(heard))
    found = {k for k, rx in _RELATIONSHIPS.items() if re.search(rx, said)}
    if not found:
        return "UNCLEAR"
    return "MATCH" if declared.strip().lower() in found else "DIFFERENT"


# ── Spoken as written ──────────────────────────────────────────────────
# [ABOGADO] The obligation wording must be approved before this is switched on.

def say_why(applicant: str) -> str:
    return (f"Gracias. Le llamo porque {applicant} le propuso como su aval en una solicitud de préstamo. "
            "¿Conoce a esta persona, y qué relación tienen?")


def say_obligation(applicant_first: str) -> str:
    return (f"Ser aval significa que, si {applicant_first} no paga su préstamo, a usted se le puede pedir ese pago. "
            "Esta llamada no le obliga a nada: para ser aval tendría que firmar un documento aparte. ¿Queda claro?")


SAY_DECISION = "¿Está de acuerdo, por su propia voluntad, en que sigamos con el proceso para que sea aval?"
SAY_UNKNOWN = "Entendido. Se lo comunico al equipo para que lo revise. Disculpe la molestia."
SAY_RESULT = {
    ACCEPTS: "Gracias. Queda anotado para que el equipo lo revise; todavía no queda como aval. "
             "¿Tiene alguna duda o comentario?",
    DECLINES: "Entendido, no hay ningún problema. Queda anotado. ¿Tiene alguna duda o comentario?",
    UNSURE: "Entendido, no tiene que decidir ahora. El equipo le dará seguimiento. ¿Tiene alguna duda o comentario?",
    DOES_NOT_KNOW: SAY_UNKNOWN,
}
