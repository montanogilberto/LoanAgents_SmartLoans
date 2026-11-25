"""A loan's current situation, computed in code — the model never does the math.

When the figures can't be trusted the result is a HOLD without any amount: asking
for money that was already sent, or the wrong sum, is worse than not asking.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from tools.backend_api import _post, get_client_loans

UNPAID = {"pending", "failed", "delinquent"}
SETTLED = {"paid", "waived"}
WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
PROMISE_WINDOW_DAYS = 8     # today + 7


def hermosillo_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=7)


def next_days(today: date) -> list[dict]:
    return [{"date": d.isoformat(), "weekday": WEEKDAYS_ES[d.weekday()]}
            for d in (today + timedelta(days=i) for i in range(PROMISE_WINDOW_DAYS))]


def _hold(reason: str) -> dict:
    return {"status": "hold", "reason": reason}


def build_situation(installments: list[dict], payments: list[dict], today: date) -> dict:
    """status: hold | settled | current | overdue."""
    if not installments:
        return _hold("no_schedule")
    states = {str(p.get("status") or "").upper() for p in payments}
    if states & {"PENDING_CONFIRMATION", "ESCALATED"}:
        return _hold("payment_under_review")
    if "REJECTED" in states:
        return _hold("rejected_payment_needs_review")    # it can be disputed
    if any(str(p.get("paymentType") or "").upper() in ("PARTIAL", "PAYOFF")
           and str(p.get("status") or "").upper() == "CONFIRMED" for p in payments):
        # Not tied to one installment: the schedule may overstate what is owed.
        return _hold("partial_payment_needs_review")
    unpaid = []
    for i in installments:
        status = str(i.get("status") or "").lower()
        if status in SETTLED:
            continue
        if status not in UNPAID:
            return _hold("unknown_installment_status")
        unpaid.append((date.fromisoformat(str(i["dueDate"])[:10]), Decimal(str(i["amount"]))))
    if not unpaid:
        return {"status": "settled"}
    unpaid.sort()
    overdue = [(d, a) for d, a in unpaid if d < today]
    upcoming = [(d, a) for d, a in unpaid if d >= today]
    out = {"status": "overdue" if overdue else "current",
           "asOf": today.isoformat(),
           "overdueCount": len(overdue),
           "overdueAmount": float(sum((a for _, a in overdue), Decimal(0))),
           "oldestDueDate": overdue[0][0].isoformat() if overdue else None,
           "daysLate": (today - overdue[0][0]).days if overdue else 0,
           "nextDueDate": upcoming[0][0].isoformat() if upcoming else None,
           "nextDueAmount": float(upcoming[0][1]) if upcoming else None,
           "daysToNextDue": (upcoming[0][0] - today).days if upcoming else None,
           "pendingInstallments": len(unpaid)}
    return out


def fetch_situation(company_id: int, client_id: int, loan_id: int) -> dict:
    """Read-only. Any failure or a loan that isn't this borrower's → hold."""
    try:
        mine = [l for l in get_client_loans(client_id, company_id)
                if l.get("loanId") == loan_id and l.get("myRole") == "borrower"]
        if not mine:
            return _hold("loan_not_of_this_borrower")
        schedule = _post("/automated-payments/schedule", {"loanId": loan_id, "companyId": company_id})
        paid = _post("/loanPayments", {"loanPayments": [
            {"action": "list", "companyId": company_id, "loanId": loan_id}]})
        # A missing key is an error answer, not "no payments".
        return build_situation(schedule["installments"],
                               [p for p in paid["loanPayments"] if p.get("loanId") == loan_id],
                               hermosillo_now().date())
    except Exception:  # noqa: BLE001 — never ask for money on unverified data
        return _hold("data_unavailable")


def check_promise(fields: dict, situation: dict, today: date) -> list[str]:
    """Errors for the model, or [] when amount, date and weekday all fit."""
    if situation.get("status") != "overdue":
        return ["there is no verified overdue amount: no promise can be recorded"]
    errors = []
    days = {d["date"]: d["weekday"] for d in next_days(today)}
    day, weekday = fields.get("date"), str(fields.get("weekday") or "").strip().lower()
    if day not in days:
        errors.append(f"date must be one of nextDays ({', '.join(days)}); for a later date use REQUEST_HUMAN")
    elif days[day] != weekday:
        right = next((d for d, w in days.items() if w == weekday), None)
        errors.append(f"{day} is {days[day]}, not {weekday}; {weekday} is {right}")
    amount = fields.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        errors.append("amount must be a number")
    elif not 0 < amount <= situation["overdueAmount"]:
        errors.append(f"amount must be above 0 and at most {situation['overdueAmount']:.2f}")
    return errors
