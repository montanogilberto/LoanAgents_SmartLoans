"""
Operational notices (avisos operativos) — temporary situations a business publishes
from the POS: "hoy cerramos por falta de agua, reabrimos mañana", a special holiday
schedule, a service that is down. They win over the regular schedule
(get_reservation_hours) while they apply, and stop applying by themselves after
their endDate.

Read from smartloans_backend /active_operationalNotices (X-Worker-Key) — notices
that apply today or start within the next days, for one company (+ branch).
The agents never write them.

Three uses, all deterministic code except the first:
- context_notices(): the list put in each turn's context, so the model sees them
  on every message without having to decide to call a tool;
- filter_slots(): get_available_slots never offers a slot inside a closure that
  blocks reservations;
- blocking_notice(): propose_action refuses a CREATE_RESERVATION inside one.

Dates and times are Hermosillo local (UTC-7, no DST), like every date here.
"""
from __future__ import annotations

import logging
import time

from tools.backend_api import _post

logger = logging.getLogger(__name__)

# A notice published in the POS reaches the agent within this many seconds.
_CACHE_SECONDS = 60
_cache: dict[tuple[int, int | None], tuple[float, list[dict]]] = {}


def fetch_active_notices(company_id: int | None, branch_id: int | None = None) -> list[dict] | None:
    """Active notices for the company (+ branch), cached briefly. None = couldn't
    check (backend down): callers fail open — the regular schedule still applies."""
    if not company_id:
        return []
    key = (int(company_id), int(branch_id) if branch_id else None)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    body = {"companyId": key[0]}
    if key[1]:
        body["branchId"] = key[1]
    try:
        result = _post("/active_operationalNotices", {"operationalNotices": [body]})
    except Exception as e:  # noqa: BLE001 — a missing notice must never break a customer's turn
        logger.warning("[operational_notices] fetch failed for company %s: %s: %s",
                       key[0], type(e).__name__, str(e)[:200])
        return hit[1] if hit else None
    rows = result.get("operationalNotices") if isinstance(result, dict) else None
    notices = [r for r in (rows or []) if isinstance(r, dict) and r.get("customerMessage")]
    _cache[key] = (time.monotonic(), notices)
    return notices


def context_notices(notices: list[dict] | None) -> list[dict]:
    """What the model sees in the turn's context (no ids, no internal titles)."""
    out = []
    for n in notices or []:
        item = {
            "type": n.get("noticeType"),
            "message": str(n.get("customerMessage"))[:1000],
            "startDate": n.get("startDate"),
            "endDate": n.get("endDate"),
            "appliesToday": bool(n.get("appliesToday")),
            "blocksReservations": bool(n.get("blocksReservations")),
        }
        if n.get("startTime") and n.get("endTime"):
            item["startTime"], item["endTime"] = n["startTime"], n["endTime"]
        out.append(item)
    return out


def _covers(notice: dict, date: str, time_slot: str | None = None) -> bool:
    """Does a reservation-blocking notice cover this day (and time, when it has hours)?"""
    if not notice.get("blocksReservations"):
        return False
    if not (str(notice.get("startDate") or "") <= date <= str(notice.get("endDate") or "")):
        return False
    start, end = notice.get("startTime") or "", notice.get("endTime") or ""
    if not (start and end) or time_slot is None:
        return True
    return start <= time_slot < end


def blocking_notice(notices: list[dict] | None, date: str, time_slot: str | None = None) -> dict | None:
    """The first notice that forbids booking `date` (at `time_slot`), else None."""
    for n in notices or []:
        if _covers(n, date, time_slot):
            return n
    return None


def filter_slots(availability: dict, notices: list[dict] | None) -> dict:
    """get_available_slots result minus the slots inside a blocking notice. A whole-day
    closure empties the day and says why (closedByNotice = the business's message)."""
    if not isinstance(availability, dict) or not notices or "slots" not in availability:
        return availability
    date = str(availability.get("date") or "")
    whole_day = next((n for n in notices if _covers(n, date) and not (n.get("startTime") and n.get("endTime"))), None)
    if whole_day:
        return {**availability, "slots": [], "closedByNotice": whole_day.get("customerMessage")}
    slots = [s for s in availability.get("slots") or []
             if not (isinstance(s, dict) and blocking_notice(notices, date, str(s.get("timeSlot") or "")))]
    if len(slots) == len(availability.get("slots") or []):
        return availability
    partial = next(n for n in notices if _covers(n, date))
    return {**availability, "slots": slots, "limitedByNotice": partial.get("customerMessage")}
