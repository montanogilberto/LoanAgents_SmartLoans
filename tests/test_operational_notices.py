"""Operational notices (POS "Avisos operativos"): closures win over the regular schedule,
and the code — not only the prompt — keeps the agent from booking inside one."""
from types import SimpleNamespace

import pytest

from tools import operational_notices as on
from tools.pending_actions import propose_action

WATER = {
    "operationalNoticeId": 7, "branchId": None, "title": "Cierre por falta de agua", "noticeType": "cierre",
    "customerMessage": "Hoy permanecemos cerrados por falta de agua. Reabrimos mañana en horario habitual.",
    "startDate": "2026-11-18", "endDate": "2026-11-18", "startTime": "", "endTime": "",
    "blocksReservations": 1, "appliesToday": 1,
}
MORNING = {**WATER, "operationalNoticeId": 8, "noticeType": "horario_especial",
           "customerMessage": "El jueves abrimos a las 12:00.", "startDate": "2026-11-19",
           "endDate": "2026-11-19", "startTime": "08:00", "endTime": "12:00", "appliesToday": 0}
INFO = {**WATER, "operationalNoticeId": 9, "noticeType": "aviso", "blocksReservations": 0}


@pytest.fixture(autouse=True)
def _clear_cache():
    on._cache.clear()
    yield
    on._cache.clear()


def _slots(date, *times):
    return {"date": date, "reservationServiceId": 5, "slots": [{"timeSlot": t, "available": 2} for t in times]}


def test_whole_day_closure_empties_the_day_and_says_why():
    out = on.filter_slots(_slots("2026-11-18", "09:00", "10:00"), [WATER])
    assert out["slots"] == [] and out["closedByNotice"] == WATER["customerMessage"]


def test_closure_does_not_touch_other_days():
    original = _slots("2026-11-19", "09:00")
    assert on.filter_slots(original, [WATER]) is original


def test_partial_hours_drop_only_the_slots_inside_the_window():
    out = on.filter_slots(_slots("2026-11-19", "09:00", "11:30", "12:00", "15:00"), [MORNING])
    assert [s["timeSlot"] for s in out["slots"]] == ["12:00", "15:00"]
    assert out["limitedByNotice"] == MORNING["customerMessage"]


def test_info_notice_never_blocks_reservations():
    original = _slots("2026-11-18", "09:00")
    assert on.filter_slots(original, [INFO]) is original
    assert on.blocking_notice([INFO], "2026-11-18", "09:00") is None


def test_context_hides_ids_and_titles():
    [item] = on.context_notices([MORNING])
    assert "operationalNoticeId" not in item and "title" not in item
    assert item == {"type": "horario_especial", "message": MORNING["customerMessage"], "startDate": "2026-11-19",
                    "endDate": "2026-11-19", "appliesToday": False, "blocksReservations": True,
                    "startTime": "08:00", "endTime": "12:00"}


def test_fetch_fails_open_and_caches(monkeypatch):
    calls = []

    def fake_post(path, body):
        calls.append((path, body))
        return {"operationalNotices": [WATER]}

    monkeypatch.setattr(on, "_post", fake_post)
    assert on.fetch_active_notices(1, 3) == [WATER]
    assert on.fetch_active_notices(1, 3) == [WATER]
    assert calls == [("/active_operationalNotices", {"operationalNotices": [{"companyId": 1, "branchId": 3}]})]

    def boom(path, body):
        raise TimeoutError("slow backend")

    monkeypatch.setattr(on, "_post", boom)
    assert on.fetch_active_notices(2) is None  # unknown → callers use the regular schedule


def _ctx(company_id=1, branch_id=None):
    return SimpleNamespace(state={"wa_company_id": company_id, "wa_branch_id": branch_id})


def test_propose_reservation_inside_a_closure_is_refused(monkeypatch):
    monkeypatch.setattr(on, "_post", lambda path, body: {"operationalNotices": [WATER]})
    ctx = _ctx()
    fields = {"reservationServiceId": 5, "reservationDate": "2026-11-18", "timeSlot": "10:00", "clientName": "Ana"}
    out = propose_action("CREATE_RESERVATION", fields, "Reservar lavado hoy 10:00", ctx)
    assert out["proposed"] is False and "falta de agua" in out["errors"][0]
    assert "pending_action" not in ctx.state


def test_propose_reservation_on_another_day_goes_through(monkeypatch):
    monkeypatch.setattr(on, "_post", lambda path, body: {"operationalNotices": [WATER]})
    ctx = _ctx()
    fields = {"reservationServiceId": 5, "reservationDate": "2026-11-19", "timeSlot": "10:00", "clientName": "Ana"}
    assert propose_action("CREATE_RESERVATION", fields, "Reservar mañana 10:00", ctx) == {"proposed": True}


def test_pos_proposals_never_fetch_notices(monkeypatch):
    def must_not_call(path, body):
        raise AssertionError("POS agents have no WhatsApp company in state")

    monkeypatch.setattr(on, "_post", must_not_call)
    ctx = SimpleNamespace(state={})
    assert propose_action("REQUEST_HUMAN", {"reason": "x"}, "Persona", ctx)["proposed"] is True


def test_agent_filters_available_slots(monkeypatch):
    from agents.whatsapp_reservations.agent import _apply_operational_notices

    monkeypatch.setattr(on, "_post", lambda path, body: {"operationalNotices": [WATER]})
    tool = SimpleNamespace(name="get_available_slots")
    out = _apply_operational_notices(tool, {}, _ctx(), _slots("2026-11-18", "09:00"))
    assert out["slots"] == [] and out["closedByNotice"]
    other = SimpleNamespace(name="get_promotions")
    assert _apply_operational_notices(other, {}, _ctx(), {"promotions": []}) is None
