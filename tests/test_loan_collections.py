"""Loan collections: figures, contact rules, gates. No network, no model."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import collections_api
from agents.collections_voice import agent as voice_agent
from agents.collections_whatsapp import agent as wa_agent
from loan_collections import situation as sit
from loan_collections.reminders import build_reminder, may_contact
from voice import collections_bridge as bridge
from voice import twilio_bridge
from voice.call_token import sign_call_token

TODAY = date(2026, 11, 23)   # Monday
TEN_AM = datetime(2026, 11, 23, 10)


def inst(due, status="pending", amount=1500):
    return {"dueDate": due, "status": status, "amount": amount}


def overdue():
    return sit.build_situation([inst("2026-10-11", "paid"), inst("2026-11-11", "delinquent", 1000.10),
                                inst("2026-11-20", "failed", 500.20), inst("2026-12-11")], [], TODAY)


def test_overdue_figures_are_exact():
    s = overdue()
    assert (s["status"], s["overdueAmount"], s["overdueCount"], s["daysLate"]) == ("overdue", 1500.30, 2, 12)
    assert s["nextDueDate"] == "2026-12-11"


def test_due_today_is_not_late_and_settled():
    assert sit.build_situation([inst("2026-11-23")], [], TODAY)["status"] == "current"
    assert sit.build_situation([inst("2026-10-01", "waived")], [], TODAY)["status"] == "settled"


@pytest.mark.parametrize("payment", [
    {"status": "PENDING_CONFIRMATION"}, {"status": "ESCALATED"}, {"status": "REJECTED"},
    {"status": "CONFIRMED", "paymentType": "PARTIAL"}])
def test_uncertain_payments_hold_without_figures(payment):
    s = sit.build_situation([inst("2026-11-11")], [payment], TODAY)
    assert s["status"] == "hold" and "overdueAmount" not in s


def test_unknown_status_and_no_schedule_hold():
    assert sit.build_situation([inst("2026-11-11", "weird")], [], TODAY)["status"] == "hold"
    assert sit.build_situation([], [], TODAY)["status"] == "hold"


def test_fetch_holds_on_other_borrower_error_and_missing_key(monkeypatch):
    monkeypatch.setattr(sit, "get_client_loans", lambda c, co: [{"loanId": 7, "myRole": "lender"}])
    assert sit.fetch_situation(1008, 5, 7)["reason"] == "loan_not_of_this_borrower"
    monkeypatch.setattr(sit, "get_client_loans", lambda c, co: [{"loanId": 7, "myRole": "borrower"}])
    monkeypatch.setattr(sit, "_post", lambda p, b: {"error": "x"})
    assert sit.fetch_situation(1008, 5, 7)["reason"] == "data_unavailable"


@pytest.mark.parametrize("now,contacts,out,why", [
    (TEN_AM, 0, False, None), (TEN_AM, 1, False, "already_contacted_today"), (TEN_AM, 0, True, "opted_out"),
    (datetime(2026, 11, 23, 20), 0, False, "outside_hours"), (datetime(2026, 11, 22, 10), 0, False, "sunday")])
def test_contact_rules(now, contacts, out, why):
    assert may_contact(now, contacts, out) == why


def test_reminder_texts():
    r = build_reminder(overdue(), "Ana", "Prueba")
    assert "$1,500.30 MXN" in r["whatsapp"] and "12 días" in r["whatsapp"]
    assert "1,500" not in r["sms"] and "préstamo" not in r["sms"].lower()
    assert not any(line.startswith(("* ", "- ")) for line in r["whatsapp"].splitlines())
    due = sit.build_situation([inst("2026-11-26")], [], TODAY)
    assert build_reminder(due, "Ana", "Prueba")["stage"] == "T-3"
    assert build_reminder(sit.build_situation([inst("2026-12-26")], [], TODAY), "Ana", "P") is None


def test_promise_checks():
    s = overdue()
    ok = {"amount": 1500.30, "weekday": "viernes", "date": "2026-11-27"}
    assert sit.check_promise(ok, s, TODAY) == []
    for bad in ({"amount": 1600}, {"amount": 0}, {"weekday": "lunes"}, {"date": "2027-01-01"}, {"amount": True}):
        assert sit.check_promise({**ok, **bad}, s, TODAY)
    assert sit.check_promise(ok, {"status": "hold"}, TODAY)


@pytest.mark.parametrize("heard,ok", [
    ("Ana Hernández López", True), ("sí, soy ana hernandes", True), ("Sí, soy yo", False), ("Ana", False),
    ("no, Ana Hernández no está", False), ("Pedro Hernández", False), ("<noise>", False)])
def test_identity_needs_first_name_and_surname(heard, ok):
    assert voice_agent._says_full_name(heard, "Ana Hernández López") is ok


def test_voice_promise_needs_identity():
    ctx = SimpleNamespace(state={"coll_situation": overdue()})
    out = voice_agent.propose_action("PROMISE_TO_PAY", {}, "", ctx)
    assert out["errors"] == ["identity is not confirmed"]


def test_other_capabilities_refused():
    ctx = SimpleNamespace(state={})
    for mod in (voice_agent, wa_agent):
        assert mod.propose_action("CREATE_INCOME", {}, "", ctx)["proposed"] is False


def test_hold_reason_never_reaches_the_model():
    ctx = SimpleNamespace(state={"coll_situation": {"status": "hold", "reason": "x"}})
    assert wa_agent.get_my_loan_situation(ctx) == {"status": "hold"}


GOOD = {"purpose": "collections", "loanId": 7, "customerName": "Ana Hernández", "contactsToday": 0, "optedOut": False}


def test_call_refusals():
    assert bridge.refusal(GOOD, {"status": "overdue"}, TEN_AM) is None
    for bad in ({"purpose": "x"}, {"loanId": None}, {"customerName": "Ana"}, {"contactsToday": None},
                {"optedOut": None}, {"optedOut": True}, {"contactsToday": 1}):
        assert bridge.refusal({**GOOD, **bad}, {"status": "overdue"}, TEN_AM)
    for status in ("hold", "current", "settled"):
        assert bridge.refusal(GOOD, {"status": status}, TEN_AM)


def test_context_hides_surname_and_loan():
    text = str(bridge.build_call_context({**GOOD, "companyName": "P"}, TEN_AM))
    assert "Hernández" not in text and "loan" not in text


def test_secretary_is_a_separate_agent():
    assert twilio_bridge._runner.agent.name == "voice_secretary_agent"
    assert bridge._runner.agent.name == "collections_voice_agent"


@pytest.mark.parametrize("secret", [None, "collections-secret"])
def test_secretary_token_or_no_secret_is_refused(monkeypatch, secret):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("COLLECTIONS_CALL_SECRET", raising=False)
    if secret:
        monkeypatch.setenv("COLLECTIONS_CALL_SECRET", secret)
    monkeypatch.setattr(bridge, "fetch_situation", lambda *a: pytest.fail("must not read the loan"))
    app = FastAPI()
    app.include_router(bridge.router)
    from starlette.websockets import WebSocketDisconnect
    with TestClient(app).websocket_connect("/voice/collections-stream") as ws:
        ws.send_json({"event": "start", "start": {"customParameters": {
            "token": sign_call_token({**GOOD, "companyId": 1008, "clientId": 5}, "secretary-secret")}}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


BODY = {"companyId": 1008, "clientId": 5, "loanId": 7, "companyName": "P", "firstName": "Ana",
        "contactsToday": 0, "optedOut": False}


def client():
    app = FastAPI()
    app.include_router(collections_api.router)
    return TestClient(app)


def test_api_is_off_by_default_and_needs_the_key(monkeypatch):
    monkeypatch.delenv("COLLECTIONS_ENABLED", raising=False)
    monkeypatch.setattr(collections_api.settings, "WORKER_KEY", "k")
    assert client().post("/collections/reminder-preview", json=BODY, headers={"X-Worker-Key": "k"}).status_code == 503
    monkeypatch.setenv("COLLECTIONS_ENABLED", "1")
    assert client().post("/collections/reminder-preview", json=BODY).status_code == 401
    monkeypatch.setattr(collections_api.settings, "WORKER_KEY", "")
    assert client().post("/collections/reminder-preview", json=BODY).status_code == 503


def test_preview_sends_nothing(monkeypatch):
    monkeypatch.setenv("COLLECTIONS_ENABLED", "1")
    monkeypatch.setattr(collections_api.settings, "WORKER_KEY", "k")
    monkeypatch.setattr(collections_api, "hermosillo_now", lambda: TEN_AM)
    monkeypatch.setattr(collections_api, "fetch_situation", lambda *a: overdue())
    out = client().post("/collections/reminder-preview", json=BODY, headers={"X-Worker-Key": "k"}).json()
    assert out["sent"] is False and out["reminder"]["stage"] == "OVERDUE"
    monkeypatch.setattr(collections_api, "fetch_situation", lambda *a: {"status": "hold", "reason": "payment_under_review"})
    out = client().post("/collections/reminder-preview", json=BODY, headers={"X-Worker-Key": "k"}).json()
    assert out["reminder"] is None and out["blockedBy"] == "payment_under_review"


def test_hang_up_is_held_once_when_nothing_was_recorded():
    ctx = SimpleNamespace(state={"voice_identity_confirmed": True})
    assert voice_agent.end_call(ctx)["ending"] is False
    assert voice_agent.end_call(ctx) == {"ending": True}
    assert voice_agent.end_call(SimpleNamespace(state={}))["ending"] is True


@pytest.mark.parametrize("msg,reason", [
    ("ya no me escriban", "pide no ser contactado"), ("dejen de llamarme", "pide no ser contactado"),
    ("STOP", "pide no ser contactado"), ("no soy Ana", "número equivocado"),
    ("número equivocado", "número equivocado"), ("pago el viernes", None), ("no puedo pagar", None),
    ("¿me pueden llamar mañana?", None), ("mándame los datos", None)])
def test_fixed_answers(msg, reason):
    out = collections_api.fixed_answer(msg, "Ana")
    assert (out["pendingAction"]["fields"]["reason"] if out else None) == reason


def test_handoff_safety_net():
    h = collections_api.ensure_handoff
    p = {"capability": "PROMISE_TO_PAY", "fields": {}}
    assert h("ya pagué ayer", "ok", p, {"status": "overdue"})["capability"] == "REQUEST_HUMAN"
    assert h("hola", "x", None, {"status": "hold"})["capability"] == "REQUEST_HUMAN"
    assert h("x", "Un asesor revisará tu caso", None, {"status": "overdue"})["capability"] == "REQUEST_HUMAN"
    assert h("pago el viernes", "¿Es correcto?", p, {"status": "overdue"}) is p
    assert h("hola", "¿Cuándo puedes pagar?", None, {"status": "overdue"}) is None


def test_every_confirmed_call_leaves_an_outcome():
    def run(text, proposals, confirmed=True):
        r = bridge.CallResult("s", 1, 2, 3, identityConfirmed=confirmed, proposals=list(proposals),
                              transcript=[{"speaker": "customer", "text": text}])
        bridge.ensure_outcome(r)
        return [p["capability"] for p in r.proposals]
    promise = [{"capability": "PROMISE_TO_PAY", "fields": {}}]
    assert run("no sé", []) == ["REQUEST_HUMAN"]
    assert run("ya pagué ayer", promise) == ["REQUEST_HUMAN"]
    assert run("el viernes", promise) == ["PROMISE_TO_PAY"]
    assert run("ya pagué", [], confirmed=False) == []


def test_identity_waits_for_the_whole_name(monkeypatch):
    import asyncio
    from agents.collections_voice import live_state
    sid = "t-frag"
    live_state.register(sid)
    monkeypatch.setattr(live_state, "session_id_of", lambda c: sid)
    live_state.add_customer_text(sid, "Soy Ana")
    ctx = SimpleNamespace(state={"coll_customer_name": "Ana Hernández López", "coll_situation": overdue()})

    async def run():
        task = asyncio.create_task(voice_agent.confirm_customer_identity("x", ctx))
        await asyncio.sleep(0.3)
        live_state.add_customer_text(sid, " Hernández López")
        return await task
    out = asyncio.run(run())
    assert out["confirmed"] and "1,500.30 pesos" in out["loan"]["say"]
    live_state.add_customer_text(sid, " ya pagué ayer")
    out = asyncio.run(voice_agent.confirm_customer_identity("x", ctx))
    assert out["alreadyPaid"] and "loan" not in out
    assert ctx.state["pending_action"]["capability"] == "REQUEST_HUMAN"
    ok = {"amount": 1, "weekday": "viernes", "date": "2026-11-27"}
    assert voice_agent.propose_action("PROMISE_TO_PAY", ok, "", ctx)["proposed"] is False
    live_state.unregister(sid)


def test_opening_identifies_the_assistant_and_unverified_calls_leave_a_note():
    opening = bridge.build_call_context({**GOOD, "companyName": "P"}, TEN_AM)["opening"]
    assert "asistente virtual" in opening and "grabada" in opening and "Hernández" not in opening
    r = bridge.CallResult("s", 1, 2, 3, toolTrace=[{"tool": "confirm_customer_identity"}])
    bridge.ensure_outcome(r)
    assert r.proposals[0]["fields"]["reason"] == "no se pudo verificar la identidad"


@pytest.mark.parametrize("text,paid", [
    ("ya pagué ayer", True), ("ayer mismo hice el pago por transferencia", True), ("ya está pagado", True),
    ("ya lo deposité", True), ("pago el viernes", False), ("no hice el pago", False), ("no pagué", False),
    ("ya mero pago", False), ("voy a hacer el pago", False)])
def test_says_paid(text, paid):
    from loan_collections.handoff import SAYS_PAID
    assert bool(SAYS_PAID.search(text)) is paid


def test_identity_retries_before_rejecting(monkeypatch):
    import asyncio
    from agents.collections_voice import live_state
    live_state.register("t-r")
    monkeypatch.setattr(live_state, "session_id_of", lambda c: "t-r")
    ctx = SimpleNamespace(state={"coll_customer_name": "Ana Hernández López"})
    outs = [asyncio.run(voice_agent.confirm_customer_identity("Ana Hernández", ctx)) for _ in range(3)]
    assert [bool(o.get("retry")) for o in outs] == [True, True, False]
    assert not any(o["confirmed"] for o in outs)
    live_state.unregister("t-r")
