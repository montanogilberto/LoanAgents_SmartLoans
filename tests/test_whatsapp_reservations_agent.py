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


def test_prompt_closing_flow_ends_with_best_promotion():
    from agents.whatsapp_reservations.prompt import INSTRUCTION
    assert "¿Te funcionó la respuesta?" in INSTRUCTION
    assert "\"1. Sí\" and \"2. No\"" in INSTRUCTION
    assert "¿Hay algo más en lo que te pueda ayudar?" in INSTRUCTION
    assert "mention the ONE that fits" in INSTRUCTION
    # a bare "sí" re-proposes only after a "¿Confirmas?", not after "¿Te funcionó?"
    assert 'If your previous message asked "¿Confirmas?"' in INSTRUCTION


def test_business_info_marks_missing_fields(monkeypatch):
    import tools.backend_api as api
    monkeypatch.setattr(api, "_post", lambda *_: {"result": [{"businessInfo": [{"address": None, "phone": "662"}]}]})
    info = api.get_business_info(1)
    assert info["address"].startswith("NO REGISTRADO") and info["phone"] == "662"


def test_prompt_shares_website_from_business_data_only():
    from agents.whatsapp_reservations.prompt import INSTRUCTION
    assert "If websiteUrl is set, share it as a\n  plain URL" in INSTRUCTION
    assert "http" not in INSTRUCTION  # the URL is business data, never in the prompt


ASK_FEEDBACK = "Abrimos a las 10.\n\n¿Te funcionó la respuesta?\n1. Sí\n2. No"
ASK_MORE = "¿Hay algo más en lo que te pueda ayudar?\n1. Sí\n2. No"


def test_conversation_stage_tracks_the_closing_flow():
    from main import _conversation_stage
    assert _conversation_stage("", "a qué hora abren", ASK_FEEDBACK, None) == ("feedback_asked", None)
    assert _conversation_stage(ASK_FEEDBACK, "1", ASK_MORE, None) == ("more_asked", "yes")
    assert _conversation_stage(ASK_FEEDBACK, "gracias 👍", ASK_MORE, None) == ("more_asked", "yes")
    assert _conversation_stage(ASK_MORE, "no gracias", "¡Que tengas buen día! Tenemos 2X1.", None) == ("closed", None)
    assert _conversation_stage(ASK_MORE, "2", "Hasta pronto.", None) == ("closed", None)


def test_conversation_stage_flags_unhappy_handoff_and_open_chats():
    from main import _conversation_stage
    assert _conversation_stage(ASK_FEEDBACK, "2", "¿Qué faltó?", None) == ("open", "no")
    assert _conversation_stage("", "quiero hablar con alguien", "Te contactan pronto.", "REQUEST_HUMAN") == ("handoff", None)
    assert _conversation_stage("", "x", "Lavado mañana 10:00. ¿Confirmas?", "CREATE_RESERVATION") == ("awaiting_confirmation", None)
    # a reply that leaves the customer without a question or goodbye isn't closed
    assert _conversation_stage("", "hola", "Hola, ¿en qué te ayudo?", None) == ("open", None)
    assert _conversation_stage("Abrimos a las 10.", "ok", "Perfecto.", None) == ("open", None)


def test_reservations_route_reports_stage(monkeypatch):
    import main
    from fastapi.testclient import TestClient

    async def run_async(*, user_id, session_id, new_message):
        from types import SimpleNamespace
        from google.genai.types import Content, Part
        yield SimpleNamespace(partial=False, actions=None, is_final_response=lambda: True,
                              content=Content(role="model", parts=[Part(text=ASK_FEEDBACK)]))
    monkeypatch.setattr(main._whatsapp_reservations_runner, "run_async", run_async)

    async def no_topic(*_):
        return None
    monkeypatch.setattr(main, "_classify_whatsapp_topic", no_topic)
    data = TestClient(main.app).post("/support/whatsapp-reservations", json={
        "companyId": 1, "phone": "5266200099999", "message": "a qué hora abren"}).json()
    assert data["conversationStage"] == "feedback_asked" and data["feedback"] is None


def test_closing_options_only_under_closing_questions():
    from main import _fix_closing_options
    assert _fix_closing_options(ASK_FEEDBACK) == ASK_FEEDBACK
    assert _fix_closing_options(ASK_MORE) == ASK_MORE
    # added when missing
    assert _fix_closing_options("Abrimos a las 10.\n\n¿Te funcionó la respuesta?") == ASK_FEEDBACK
    # dropped under any other question
    offer = "No tengo ese dato. ¿Te comunico con alguien del equipo?\n1. Sí\n2. No"
    assert _fix_closing_options(offer) == "No tengo ese dato. ¿Te comunico con alguien del equipo?"
    both = ("¿Te paso con alguien?\n1. Sí\n2. No\n\nTambién mira la web.\n\n"
            "¿Te funcionó la respuesta?\n1. Sí\n2. No")
    assert _fix_closing_options(both) == (
        "¿Te paso con alguien?\n\nTambién mira la web.\n\n¿Te funcionó la respuesta?\n1. Sí\n2. No")


def test_closing_questions_put_back_word_for_word():
    from main import _fix_closing_options
    assert _fix_closing_options("¿Hay algo más en lo que te pueda ayudar, Gilberto?") == ASK_MORE
    assert _fix_closing_options("Listo.\n¿Hay algo más en lo que te pueda ayudar mientras esperas?") == "Listo.\n" + ASK_MORE


def test_read_tools_never_crash_the_turn(monkeypatch):
    """2026-11-02: get_reservation_services timed out after a backend restart and the
    whole turn crashed (500 → "Perdón, tuve un problema"). Read tools now report it."""
    import httpx
    import tools.backend_api as api
    from agents.whatsapp_reservations.agent import whatsapp_reservations_agent as agent

    def boom(*_a, **_k):
        raise httpx.ReadTimeout("The read operation timed out")
    monkeypatch.setattr(api, "_post", boom)
    tools = {t.func.__name__: t.func for t in agent.tools if hasattr(t, "func")}
    assert tools["get_reservation_services"](1)["status"] == "unavailable"
    assert tools["get_available_slots"](1, "2026-11-03", 5)["status"] == "unavailable"


def test_wrapped_tools_keep_their_declaration():
    """ADK builds the tool schema from name, docstring and signature: the wrapper keeps them."""
    import inspect
    from agents.whatsapp_reservations.agent import whatsapp_reservations_agent as agent
    tools = {t.func.__name__: t for t in agent.tools if hasattr(t, "func")}
    assert list(inspect.signature(tools["get_available_slots"].func).parameters) == [
        "company_id", "date", "reservation_service_id"]
    assert "tool_context" in inspect.signature(tools["get_my_reward_points"].func).parameters
    assert tools["get_reservation_services"].func.__doc__
    decl = tools["get_available_slots"]._get_declaration()
    assert decl.name == "get_available_slots" and decl.parameters.properties
