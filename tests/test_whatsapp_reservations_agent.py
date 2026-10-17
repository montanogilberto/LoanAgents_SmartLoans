"""
Smoke tests for the WhatsApp reservations agent (same pattern as
test_pos_income_support_agent.py) plus the CREATE_RESERVATION contract it
proposes — smartloans_backend/modules/whatsappReservations.py executes it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from retrieval.contracts import CONTRACTS

_VALID = {"reservationServiceId": 1, "reservationDate": "2026-09-26",
          "timeSlot": "11:00", "clientName": "Ana"}


def test_whatsapp_reservations_agent_importable():
    from agents.whatsapp_reservations.agent import whatsapp_reservations_agent
    assert whatsapp_reservations_agent.name == "whatsapp_reservations_agent"
    assert whatsapp_reservations_agent.output_key == "whatsapp_reservations_reply"


def test_whatsapp_reservations_agent_has_expected_tools():
    from agents.whatsapp_reservations.agent import whatsapp_reservations_agent
    tool_names = {t.func.__name__ for t in whatsapp_reservations_agent.tools if hasattr(t, "func")}
    assert tool_names == {"get_reservation_services", "get_reservation_hours",
                          "get_available_slots", "get_promotions", "get_business_info",
                          "get_faqs", "get_my_reward_points", "propose_action"}


def test_route_registered():
    from main import app
    assert "/support/whatsapp-reservations" in {r.path for r in app.routes}


def test_create_reservation_valid_fields_pass():
    assert CONTRACTS["CREATE_RESERVATION"].validate(_VALID) == []


def test_create_reservation_requires_service_id():
    fields = {k: v for k, v in _VALID.items() if k != "reservationServiceId"}
    errors = CONTRACTS["CREATE_RESERVATION"].validate(fields)
    assert any("reservationServiceId" in e for e in errors)


def test_create_reservation_rejects_bad_date_and_time_format():
    errors = CONTRACTS["CREATE_RESERVATION"].validate(
        {**_VALID, "reservationDate": "viernes", "timeSlot": "11am"})
    assert any("reservationDate" in e for e in errors)
    assert any("timeSlot" in e for e in errors)


def test_create_reservation_rejects_agent_supplied_company_id():
    errors = CONTRACTS["CREATE_RESERVATION"].validate({**_VALID, "companyId": 999})
    assert any("companyId" in e for e in errors)


# ── Route: sessions per branch + idle expiry (runner mocked, no LLM call) ──

def _call_route(monkeypatch, **overrides):
    import asyncio
    import main

    seen = {}

    async def fake_run_async(*, user_id, session_id, new_message):
        seen["user_id"] = user_id
        # setdefault: an empty turn is retried with an internal nudge — keep
        # the customer's (first) message.
        seen.setdefault("message", new_message.parts[0].text)
        seen["calls"] = seen.get("calls", 0) + 1
        return
        yield  # async generator with no events

    monkeypatch.setattr(main._whatsapp_reservations_runner, "run_async", fake_run_async)
    req = main.WhatsAppReservationsRequest(
        **{"companyId": 1, "phone": "+526621234567", "message": "hola", **overrides})
    resp = asyncio.run(main.support_whatsapp_reservations(req))
    return main, seen, resp


def test_route_passes_branch_to_agent_context(monkeypatch):
    _, seen, resp = _call_route(monkeypatch, branchId=1, branchName="Nuevo Hermosillo")
    assert '"branchName": "Nuevo Hermosillo"' in seen["message"]
    assert resp.pendingAction is None


def test_route_keeps_branches_in_separate_sessions(monkeypatch):
    _, a, _ = _call_route(monkeypatch, branchId=1)
    _, b, _ = _call_route(monkeypatch, branchId=2)
    assert a["user_id"] != b["user_id"]


def test_route_starts_fresh_session_after_idle(monkeypatch):
    import asyncio
    main, seen, _ = _call_route(monkeypatch, branchId=9, phone="+520000000009")
    uid = seen["user_id"]
    app = "loan_agents_whatsapp_reservations"
    session = asyncio.run(main._session_service.get_session(app_name=app, user_id=uid, session_id=uid))
    session.state["marker"] = "old"
    stored = main._session_service.sessions[app][uid][uid]
    stored.state["marker"] = "old"
    stored.last_update_time -= main._WHATSAPP_SESSION_IDLE_SECONDS + 1
    _call_route(monkeypatch, branchId=9, phone="+520000000009")
    fresh = asyncio.run(main._session_service.get_session(app_name=app, user_id=uid, session_id=uid))
    assert "marker" not in fresh.state


# ── Assistant tools: handoff contract + points only for the sender ─────────

def test_request_human_needs_a_reason():
    assert CONTRACTS["REQUEST_HUMAN"].validate({"reason": "quiere hablar con alguien"}) == []
    assert any("reason" in e for e in CONTRACTS["REQUEST_HUMAN"].validate({}))


def test_my_reward_points_has_no_phone_parameter():
    import inspect
    from tools.backend_api import get_my_reward_points
    assert list(inspect.signature(get_my_reward_points).parameters) == ["tool_context"]


def test_my_reward_points_uses_session_phone_and_worker_key(monkeypatch):
    from types import SimpleNamespace
    from unittest import mock
    import tools.backend_api as api

    resp = mock.Mock()
    resp.json.return_value = {"result": [{"status": "found", "clientId": 7, "firstName": "Ana",
                                          "balance": 40, "lifetimeEarned": 90, "redeemable": []}]}
    post = mock.Mock(return_value=resp)
    monkeypatch.setattr(api, "WORKER_KEY", "secret")
    monkeypatch.setattr(api.httpx, "post", post)
    ctx = SimpleNamespace(state={"wa_company_id": 1, "wa_customer_phone": "+526621234567"})

    out = api.get_my_reward_points(ctx)

    assert out["balance"] == 40 and "clientId" not in out
    assert post.call_args.kwargs["json"] == {"customerRewards": [{"companyId": 1, "phone": "+526621234567"}]}
    assert post.call_args.kwargs["headers"] == {"X-Worker-Key": "secret"}


def test_my_reward_points_unavailable_without_worker_key(monkeypatch):
    from types import SimpleNamespace
    import tools.backend_api as api
    monkeypatch.setattr(api, "WORKER_KEY", "")
    ctx = SimpleNamespace(state={"wa_company_id": 1, "wa_customer_phone": "+526621234567"})
    assert api.get_my_reward_points(ctx) == {"status": "unavailable"}


def test_route_stores_sender_phone_in_session_state(monkeypatch):
    import asyncio
    main, seen, _ = _call_route(monkeypatch, branchId=4, phone="+520000000044")
    uid = seen["user_id"]
    session = asyncio.run(main._session_service.get_session(
        app_name="loan_agents_whatsapp_reservations", user_id=uid, session_id=uid))
    assert session.state["wa_customer_phone"] == "+520000000044"
    assert session.state["wa_company_id"] == 1


def test_empty_model_turn_never_returns_the_previous_reply(monkeypatch):
    """Gemini sometimes ends a turn empty (a bare "3" after the menu); the
    route used to return the PREVIOUS turn's text from session.state."""
    import asyncio
    main, seen, _ = _call_route(monkeypatch, branchId=5, phone="+520000000055")
    uid = seen["user_id"]
    stored = main._session_service.sessions["loan_agents_whatsapp_reservations"][uid][uid]
    stored.state["whatsapp_reservations_reply"] = "MENU DEL TURNO ANTERIOR"
    _, seen2, resp = _call_route(monkeypatch, branchId=5, phone="+520000000055")
    assert resp.reply != "MENU DEL TURNO ANTERIOR"
    assert seen2["calls"] == 2  # retried once before falling back
