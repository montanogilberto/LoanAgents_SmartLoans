"""
Deterministic check of the day and time in a PICKUP_AGREEMENT, before it's proposed.

Found 2026-11-07 (live matrix, 39 calls): Gemini Live mapped weekdays to the wrong
date — "el lunes" proposed as 2026-11-10 (a Tuesday) and once as today, a Saturday,
at 18:00 after closing — while reading "el lunes" back to the customer. The prompt
already said to use only nextDays; a prompt can't guarantee it. So the agreement
carries the weekday the customer agreed to, and this code checks that it, the date
and the time fit the call's own calendar (built by voice/twilio_bridge.py from the
same nextDays/openingHours the model was given). An error names the correct date,
so the model fixes it in the same turn. The backend validates again on its side.
"""
from __future__ import annotations

import re

_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def build_calendar(context: dict) -> dict:
    """{today, nowTime, days: {date: weekday}, hours: {weekday: [open, close] | None} | None}."""
    opening = context.get("openingHours")
    hours = None
    if isinstance(opening, list):
        hours = {d["weekday"]: (None if d.get("closed") else [d.get("open"), d.get("close")])
                 for d in opening if isinstance(d, dict) and d.get("weekday")}
    return {"today": context.get("today"), "nowTime": context.get("nowTime"),
            "days": {d["date"]: d["weekday"] for d in context.get("nextDays") or []},
            "hours": hours}


def check_agreement_slot(fields: dict, calendar: dict | None) -> list[str]:
    """Errors for the model, or [] when date, weekday and time all fit."""
    if not calendar or not calendar.get("days"):
        return []                      # no calendar (shouldn't happen): the backend still validates
    days = calendar["days"]
    date, weekday, time = fields.get("date"), (fields.get("weekday") or "").strip().lower(), fields.get("time")
    by_weekday = {w: d for d, w in sorted(days.items(), reverse=True)}   # nearest date per weekday

    if not weekday:
        return ["weekday is required: the day of the week the customer agreed to (e.g. \"lunes\")"]
    if weekday not in by_weekday:
        return [f"weekday {weekday!r} is not one of {sorted(set(days.values()))}"]
    if date not in days:
        return [f"date {date!r} is outside the next days; {weekday} is {by_weekday[weekday]}"]
    if days[date] != weekday:
        return [f"date {date} is {days[date]}, not {weekday}. If the customer agreed to {weekday}, "
                f"the date is {by_weekday[weekday]}"]

    errors = []
    hours = calendar.get("hours")
    if time and _TIME.fullmatch(str(time)) and hours is not None and weekday in hours:
        span = hours[weekday]
        if span is None:
            errors.append(f"the business is closed on {weekday}; agree on another day")
        elif span[0] and span[1] and not (span[0] <= time < span[1]):
            errors.append(f"{time} is outside {weekday}'s hours ({span[0]}–{span[1]}); "
                          "agree on a time inside them")
    if date == calendar.get("today") and time and calendar.get("nowTime") and time <= calendar["nowTime"]:
        errors.append(f"{time} today has already passed (it's {calendar['nowTime']})")
    return errors
