"""smartScore: payment capacity by a deterministic rule (credit-assessment PRD §8.4).

    disponible  = ingreso verificado − gasto esencial − deuda
    cuotaMaxima = max(0, disponible × capRatio)

No AI here: the agent only explains this result. Each factor is PASS/REVIEW/FAIL/UNKNOWN with
its rule, value and reason; UNKNOWN is never read as 0 or as negative. The level comes from
precedence, not a sum of points. It never approves, rejects or caps what a lender decides.
Exact decimals; amounts are rounded down to cents only at the end.
"""
from __future__ import annotations

import hashlib
import json
from decimal import ROUND_FLOOR, Decimal

# Same monthly factors as the payslip review (average weeks per month; RFC-020 frequencies).
from proof_documents.review import _MONTHLY_FACTOR as MONTHLY_FACTOR

PASS, REVIEW, FAIL, UNKNOWN = "PASS", "REVIEW", "FAIL", "UNKNOWN"
FREQUENCIES = tuple(MONTHLY_FACTOR)
RULES_VERSION = "capacity-1"
_CENT = Decimal("0.01")


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_FLOOR)


def per_period(monthly: Decimal, frequency: str) -> Decimal:
    """Monthly amount → one installment of that frequency, rounded down."""
    num, den = MONTHLY_FACTOR[frequency]
    return _cents(monthly * den / num)


def inputs_hash(inputs: dict) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


def _factor(code: str, result: str, rule: str, value, reason: str) -> dict:
    return {"code": code, "result": result, "rule": rule,
            "value": None if value is None else float(value), "reason": reason}


def compute(inputs: dict) -> dict:
    """inputs: capRatio, policyVersion and the amounts (None = not known). Never raises on a gap."""
    verified = _dec(inputs.get("verifiedMonthlyIncome"))
    declared = _dec(inputs.get("declaredMonthlyIncome"))
    expenses = _dec(inputs.get("essentialExpensesMonthly"))
    debt = _dec(inputs.get("debtPaymentsMonthly"))
    cap_ratio = Decimal(str(inputs["capRatio"]))
    installment = _dec(inputs.get("requestedInstallment"))
    frequency = inputs.get("loanFrequency")

    factors = []
    if verified is not None and verified > 0:
        factors.append(_factor("INCOME", PASS, "ingreso verificado > 0", verified,
                               "Hay ingreso verificado por una persona."))
    elif declared is not None and declared > 0:
        factors.append(_factor("INCOME", REVIEW, "solo ingreso declarado", declared,
                               "El ingreso solo está declarado; falta verificarlo con un recibo de nómina."))
    else:
        factors.append(_factor("INCOME", UNKNOWN, "sin ingreso", None, "No hay ingreso declarado ni verificado."))

    for code, value, what in (("EXPENSES", expenses, "gasto esencial"), ("DEBT", debt, "pago de deudas")):
        if value is None:
            factors.append(_factor(code, UNKNOWN, f"{what} declarado", None, f"Falta declarar el {what} mensual."))
        else:
            factors.append(_factor(code, PASS, f"{what} declarado", value, f"El {what} mensual está declarado."))

    capacity = None
    if verified is None or verified <= 0 or expenses is None or debt is None:
        factors.append(_factor("CAPACITY", UNKNOWN, "ingreso verificado − gasto − deuda", None,
                               "Capacidad de pago no disponible: faltan datos."))
    else:
        available = verified - expenses - debt
        max_monthly = _cents(max(Decimal(0), available * cap_ratio))
        capacity = {
            "availableMonthly": float(_cents(available)),
            "maxInstallmentMonthly": float(max_monthly),
            "maxInstallmentByFrequency": {f: float(per_period(max_monthly, f)) for f in FREQUENCIES},
        }
        if max_monthly <= 0:
            factors.append(_factor("CAPACITY", FAIL, "disponible × capRatio > 0", available,
                                   "El gasto y las deudas igualan o superan el ingreso verificado."))
        else:
            factors.append(_factor("CAPACITY", PASS, "disponible × capRatio > 0", max_monthly,
                                   "El ingreso verificado deja margen para una cuota."))

    if installment is not None:
        if capacity is None:
            factors.append(_factor("INSTALLMENT", UNKNOWN, "cuota ≤ cuota máxima", installment,
                                   "No se puede comparar la cuota sin capacidad de pago."))
        else:
            limit = Decimal(str(capacity["maxInstallmentByFrequency"][frequency]))
            fits = installment <= limit
            factors.append(_factor("INSTALLMENT", PASS if fits else FAIL, "cuota ≤ cuota máxima", installment,
                                   "La cuota cabe en la capacidad de pago." if fits
                                   else "La cuota supera la capacidad de pago."))

    results = {f["code"]: f["result"] for f in factors}
    if FAIL in (results.get("CAPACITY"), results.get("INSTALLMENT")):
        level = "STRONG_ALERT"      # capacity insufficient: both sides acknowledge it; GMO never rejects
    elif FAIL in results.values():
        level = "HIGH_ALERT"
    elif REVIEW in results.values():
        level = "REVIEW"
    elif UNKNOWN in results.values():
        level = "INCOMPLETE"
    else:
        level = "OK"
    return {"level": level, "factors": factors, "capacity": capacity, "rulesVersion": RULES_VERSION,
            "inputsHash": inputs_hash(inputs)}
