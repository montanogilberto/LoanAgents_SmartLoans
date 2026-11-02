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
                          "get_faqs", "get_my_reward_points", "get_machine_availability",
                          "propose_action"}


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

    async def fake_topic(message, previous_reply):
        return None

    monkeypatch.setattr(main._whatsapp_reservations_runner, "run_async", fake_run_async)
    monkeypatch.setattr(main, "_classify_whatsapp_topic", fake_topic)
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


def test_send_client_qr_takes_no_fields():
    # The backend matches the client by the WhatsApp sender's phone; nothing
    # the agent passes decides whose QR goes out.
    assert CONTRACTS["SEND_CLIENT_QR"].fields == ()
    assert CONTRACTS["SEND_CLIENT_QR"].validate({}) == []


def test_menu_offers_the_qr_as_option_7():
    from agents.whatsapp_reservations.prompt import INSTRUCTION, MENU
    assert "7️⃣ Mi código QR de cliente" in MENU
    assert "6️⃣ Hablar con alguien del equipo" in MENU  # 6 still means a person
    assert "7 their QR (SEND_CLIENT_QR)" in INSTRUCTION
    assert 'propose_action(capability="SEND_CLIENT_QR"' in INSTRUCTION


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


# ── get_machine_availability (pure part) ───────────────────────────────────
from datetime import datetime, timezone  # noqa: E402

_NOW = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)


def _dev(device_id, name, **kw):
    return {"deviceId": device_id, "name": name, "machineType": "washer", "isActive": 1,
            "isOnline": 1, "lastStatus": "idle", "cycleMinutes": 45, **kw}


def _use(device_id, created, status="authorized"):
    return {"deviceId": device_id, "status": status, "created_At": created}


def test_machine_availability_states_and_order():
    from tools.backend_api import _machine_availability
    devices = [
        _dev(1, "Lavadora 1"),                                    # cycle started 30 min ago -> ~15 left
        _dev(2, "Lavadora 2"),                                    # free
        _dev(3, "Secadora 1", machineType="dryer", isOnline=0),   # offline
        _dev(4, "Lavadora 3", lastStatus="error"),                # fault
        _dev(5, "Lavadora 4", isActive=0),                        # disabled -> hidden
        _dev(6, "Lavadora 5", lastStatus="running"),              # running, no known charge
    ]
    uses = [_use(1, "2026-11-02T02:30:00"), _use(2, "2026-11-02T01:00:00"),
            _use(2, "2026-11-02T02:50:00", status="rejected")]
    r = _machine_availability(devices, uses, None, _NOW)
    assert (r["available"], r["inUse"], r["outOfService"]) == (1, 2, 2)
    by_name = {m["name"]: m for m in r["machines"]}
    assert "Lavadora 4" not in by_name
    assert by_name["Lavadora 1"] == {"name": "Lavadora 1", "type": "Lavadora", "state": "en_uso", "minutesLeft": 15}
    assert by_name["Lavadora 2"]["state"] == "disponible"           # rejected charge doesn't start a cycle
    assert by_name["Lavadora 5"] == {"name": "Lavadora 5", "type": "Lavadora", "state": "en_uso", "minutesLeft": None}
    assert by_name["Secadora 1"]["state"] == by_name["Lavadora 3"]["state"] == "fuera_de_servicio"
    assert [m["name"] for m in r["machines"]][:3] == ["Lavadora 2", "Lavadora 1", "Lavadora 5"]


def test_machine_availability_never_leaks_business_data():
    from tools.backend_api import _machine_availability
    r = _machine_availability([_dev(1, "Lavadora 1", lastIp="10.0.0.5", lastRssi=-60, priceMXN=40)],
                              [{**_use(1, "2026-11-02T02:30:00"), "clientId": 77, "amountMXN": 40}], None, _NOW)
    assert set(r["machines"][0]) == {"name", "type", "state", "minutesLeft"}
    assert "77" not in str(r) and "10.0.0.5" not in str(r)


def test_machine_availability_branch_filter_and_none():
    from tools.backend_api import _machine_availability
    devices = [_dev(1, "Centro", branchId=1), _dev(2, "Norte", branchId=2), _dev(3, "Sin sucursal")]
    names = {m["name"] for m in _machine_availability(devices, [], 2, _NOW)["machines"]}
    assert names == {"Norte", "Sin sucursal"}
    assert _machine_availability([], [], None, _NOW) == {"status": "no_machines"}
    assert _machine_availability([_dev(1, "X", isActive=0)], [], None, _NOW) == {"status": "no_machines"}


def test_prompt_shares_address_and_maps_link():
    from agents.whatsapp_reservations.prompt import INSTRUCTION
    assert "mapsUrl as-is on its own line" in INSTRUCTION
    assert "no links." not in INSTRUCTION


def test_prompt_delivery_app_pickup_sends_address():
    from agents.whatsapp_reservations.prompt import INSTRUCTION
    assert "DiDi, Uber" in INSTRUCTION
    assert "El conductor debe bajarse a recoger el\n  producto en la sucursal" in INSTRUCTION
