"""Handoffs decided in code, shared by both collections agents."""
import re

# Broad on purpose: a false positive only sends the case to a person.
SAYS_PAID = re.compile(
    r"(?<!no )\b(ya\s+)?((la|lo|te|les|le|se lo)\s+)?"
    r"(pagu[eé](?=\W|$)|deposit[eé](?=\W|$)|transfer[ií](?=\W|$)|liquid[eé](?=\W|$)|"
    r"(hice|mand[eé]|envi[eé]) (el|la|mi|un|una) (pago|dep[oó]sito|transferencia|comprobante))"
    r"|\bya (est[aá]|qued[oó]|fue) (pagad|liquidad|cubiert)", re.I)


def request_human(reason: str) -> dict:
    return {"capability": "REQUEST_HUMAN", "fields": {"reason": reason},
            "confirmationSummary": "Atención de una persona"}
