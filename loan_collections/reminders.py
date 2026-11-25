"""Reminder texts with the loan's current situation. Fixed templates: no model
writes amounts or dates. PREVIEW ONLY — this module has no way to send anything.

WhatsApp carries the figures; SMS doesn't (it shows on locked screens).
"""
from __future__ import annotations

from datetime import date, datetime

OPEN_HOUR, CLOSE_HOUR = 8, 20      # Hermosillo; Sundays never. Conservative: legal review pending.
MAX_CONTACTS_PER_DAY = 1

_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre"]


def may_contact(now_local: datetime, contacts_today: int, opted_out: bool) -> str | None:
    """None = allowed; otherwise why not."""
    if opted_out:
        return "opted_out"
    if now_local.weekday() == 6:
        return "sunday"
    if not OPEN_HOUR <= now_local.hour < CLOSE_HOUR:
        return "outside_hours"
    if contacts_today >= MAX_CONTACTS_PER_DAY:
        return "already_contacted_today"
    return None


def money(v: float) -> str:
    return f"${v:,.2f} MXN"


def day_es(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} de {_MONTHS[d.month - 1]} de {d.year}"


def stage(s: dict) -> str | None:
    if s["status"] == "overdue":
        return "OVERDUE"
    if s["status"] == "current":
        return {3: "T-3", 1: "T-1", 0: "DUE"}.get(s["daysToNextDue"])
    return None


def build_reminder(s: dict, first_name: str, company_name: str) -> dict | None:
    """{"stage", "whatsapp", "sms"} or None when there is nothing to remind."""
    st = stage(s)
    if st is None:
        return None
    hello = f"Hola {first_name}, te escribe el asistente virtual de *{company_name}*."
    if st == "OVERDUE":
        n, d = s["overdueCount"], s["daysLate"]
        lines = [hello, "", "Esta es la situación de tu préstamo:",
                 f"• Cuotas vencidas: {n}", f"• Monto vencido: *{money(s['overdueAmount'])}*",
                 f"• Vencimiento más antiguo: {day_es(s['oldestDueDate'])} ({d} {'día' if d == 1 else 'días'} de atraso)"]
        if s["nextDueDate"]:
            lines.append(f"• Próxima cuota: {money(s['nextDueAmount'])} el {day_es(s['nextDueDate'])}")
        lines += ["", "Si ya pagaste o no reconoces esta información, avísame y lo revisa una persona. "
                      "Si necesitas acordar una fecha, respóndeme por aquí."]
    else:
        when = {"T-3": "en 3 días", "T-1": "mañana", "DUE": "hoy"}[st]
        lines = [hello, "", f"Te recuerdo que tu cuota de *{money(s['nextDueAmount'])}* vence {when}, "
                            f"el {day_es(s['nextDueDate'])}.", "", "Si ya pagaste, avísame y lo revisamos."]
    return {"stage": st, "whatsapp": "\n".join(lines),
            "sms": f"{company_name}: Hola {first_name}, tienes un aviso en tu cuenta. "
                   "Revísalo en la app o responde para hablar con una persona."}
