"""Aval calls: identity, order, evidence of a "sí", gates. No network, no model."""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agents.aval_voice import agent, live_state, rules
from retrieval.contracts import CONTRACTS
from voice import aval_bridge as bridge
from voice.call_token import sign_call_token

TEN_AM = datetime(2026, 11, 23, 10)     # Monday
GOOD = {"purpose": "aval", "guarantorId": 7, "companyName": "P", "guarantorName": "Ana Hernández López",
        "applicantName": "Luis Ramírez", "relationship": "Familiar", "applicantConfirmedNotice": True,
        "contactsToday": 0, "optedOut": False}


def ctx(**state):
    return SimpleNamespace(state={"aval_guarantor_name": "Ana Hernández López", "aval_applicant_name": "Luis Ramírez",
                                  "aval_declared_relationship": "Familiar", **state})


def rec(c, question, answer, said):
    return asyncio.run(agent.record_answer(question, answer, said, c))


@pytest.fixture
def live(monkeypatch):
    live_state.register("t")
    monkeypatch.setattr(live_state, "session_id_of", lambda c: "t")
    monkeypatch.setattr(agent, "_TRANSCRIPT_WAIT_S", 0.0)
    yield "t"
    live_state.unregister("t")


@pytest.mark.parametrize("heard,ok", [
    ("Ana Hernández López", True), ("sí, soy ana hernandes", True), ("Sí, soy yo", False), ("Ana", False),
    ("no, Ana Hernández no está", False), ("Pedro Hernández", False), ("<noise>", False)])
def test_identity_needs_first_name_and_surname(heard, ok):
    assert rules.says_full_name(heard, "Ana Hernández López") is ok


@pytest.mark.parametrize("heard,ok", [
    ("Sí, claro", True), ("sí acepto", True), ("de acuerdo", True), ("no", False), ("sí, pero depende", False),
    ("lo tengo que pensar", False), ("creo que sí", False), ("<noise>", False), ("", False), ("¿cuánto es?", False),
    ("sí, me están presionando", False)])
def test_clear_yes(heard, ok):
    assert rules.clear_yes(heard) is ok


@pytest.mark.parametrize("declared,heard,out", [
    ("Familiar", "sí, es mi hermano", "MATCH"), ("Familiar", "es mi vecino", "DIFFERENT"),
    ("Trabajo", "somos compañeros de trabajo", "MATCH"), ("Amistad", "lo conozco", "UNCLEAR"),
    ("Otra", "es mi amigo", "NOT_COMPARED"), (None, "es mi amigo", "NOT_COMPARED")])
def test_relationship(declared, heard, out):
    assert rules.relationship_check(declared, heard) == out


@pytest.mark.parametrize("heard,stop", [
    ("ya no me llamen", True), ("no me vuelvan a llamar", True), ("dejen de molestar", True),
    ("no, me llamo Ana", False), ("no me han llamado", False), ("sí, llámenme mañana", False)])
def test_stop(heard, stop):
    assert rules.asks_to_stop(heard) is stop


def test_nothing_is_told_or_recorded_before_identity():
    c = ctx()
    assert rec(c, "relationship", "yes", "es mi hermano")["recorded"] is False
    out = asyncio.run(agent.confirm_guarantor_identity("sí soy yo", c))
    assert not out["confirmed"] and "Luis" not in str(out)
    assert "Luis Ramírez" in asyncio.run(agent.confirm_guarantor_identity("Ana Hernández", c))["say"]


def test_order_is_enforced_and_full_call_accepts():
    c = ctx(voice_identity_confirmed=True)
    assert "first" in rec(c, "decision", "yes", "sí acepto")["error"]
    assert "first" in rec(c, "understands", "yes", "sí")["error"]
    assert "no le obliga" in rec(c, "relationship", "yes", "sí, es mi hermano")["say"]
    assert rec(c, "understands", "yes", "sí, queda claro")["say"] == rules.SAY_DECISION
    assert "todavía no queda como aval" in rec(c, "decision", "yes", "sí, acepto")["say"]
    p = c.state["pending_action"]
    assert p["fields"] == {"outcome": "ACCEPTS", "knowsApplicant": "yes", "relationshipCheck": "MATCH",
                           "understandsObligation": "yes"}
    assert CONTRACTS["GUARANTOR_CALL_RESULT"].validate(p["fields"]) == []
    rec(c, "decision", "no", "mejor no")                         # changed their mind
    assert c.state["pending_action"]["fields"]["outcome"] == "DECLINES"


@pytest.mark.parametrize("steps,outcome", [
    ([("relationship", "no", "no lo conozco")], "DOES_NOT_KNOW_APPLICANT"),
    ([("relationship", "yes", "mi hermano"), ("understands", "no", "no"), ("understands", "no", "no")], "UNSURE"),
    ([("relationship", "yes", "mi hermano"), ("understands", "yes", "sí"), ("decision", "no", "no")], "DECLINES"),
    ([("relationship", "yes", "mi hermano"), ("understands", "yes", "sí"), ("decision", "unsure", "lo pienso")], "UNSURE")])
def test_other_outcomes(steps, outcome):
    c = ctx(voice_identity_confirmed=True)
    for s in steps:
        rec(c, *s)
    assert c.state["pending_action"]["fields"]["outcome"] == outcome


def test_the_models_yes_is_not_evidence(live):
    """The bridge heard an earlier sí and then noise: the model's "yes" is UNSURE."""
    c = ctx(voice_identity_confirmed=True, aval_answers={"relationship": "yes", "understands": "yes"})
    live_state.add_customer_text(live, "Sí, queda claro")
    live_state.mark_question(live)
    live_state.mark_agent_turn_end(live)
    outs = [rec(c, "decision", "yes", "sí acepto") for _ in range(2)]
    assert all(o.get("retry") for o in outs)
    assert "Repite" in rec(c, "decision", "yes", "sí acepto")["error"]
    rec(c, "decision", "yes", "sí acepto")
    assert c.state["pending_action"]["fields"]["outcome"] == "UNSURE"


def test_a_heard_yes_after_the_question_counts(live):
    c = ctx(voice_identity_confirmed=True, aval_answers={"relationship": "yes", "understands": "yes"})
    live_state.mark_question(live)
    live_state.add_customer_text(live, "Sí, de acuerdo")
    rec(c, "decision", "yes", "x")
    assert c.state["pending_action"]["fields"]["outcome"] == "ACCEPTS"


def test_hang_up_is_held_once_when_nothing_was_recorded():
    c = ctx(voice_identity_confirmed=True)
    assert agent.end_call(c)["ending"] is False
    assert agent.end_call(c) == {"ending": True}
    assert agent.end_call(ctx())["ending"] is True


def test_call_refusals():
    assert bridge.refusal(GOOD, TEN_AM) is None
    for bad in ({"purpose": "collections"}, {"guarantorId": None}, {"guarantorId": True}, {"guarantorName": "Ana"},
                {"applicantName": None}, {"companyName": ""}, {"applicantConfirmedNotice": False},
                {"contactsToday": None}, {"contactsToday": 1}, {"optedOut": None}, {"optedOut": True}):
        assert bridge.refusal({**GOOD, **bad}, TEN_AM), bad
    assert bridge.refusal(GOOD, datetime(2026, 11, 22, 10)) == "sunday"


def test_context_hides_everything_but_the_first_name():
    text = str(bridge.build_call_context(GOOD, TEN_AM)).lower()
    assert "asistente virtual" in text and "grabada" in text
    assert not any(w in text for w in ("hernández", "luis", "ramírez", "préstamo", "aval", "familiar"))


def test_outcomes_after_the_call():
    def run(text, proposals=(), confirmed=True, tools=()):
        r = bridge.CallResult("s", 1, 2, 3, identityConfirmed=confirmed, proposals=list(proposals),
                              toolTrace=[{"tool": t} for t in tools],
                              transcript=[{"speaker": "customer", "text": text}])
        bridge.ensure_outcome(r)
        return r
    accept = {"capability": "GUARANTOR_CALL_RESULT", "fields": {"outcome": "ACCEPTS"}}
    human = {"capability": "REQUEST_HUMAN", "fields": {"reason": "x"}}
    assert run("sí", [accept]).outcome is accept
    assert run("sí", [accept, human]).outcome is None
    assert run("ya no me llamen", [accept]).proposals[0]["fields"]["reason"] == "pide no ser contactado"
    assert run("hola").proposals[0]["capability"] == "REQUEST_HUMAN"
    assert run("número equivocado", confirmed=False).proposals[0]["fields"]["reason"] == "número equivocado"
    assert run("x", confirmed=False, tools=["confirm_guarantor_identity"]).proposals
    assert run("buzón", confirmed=False).proposals == []
    assert "Ana" not in str(run("soy Ana", [accept]).safe_summary())


@pytest.mark.parametrize("secret", [None, "aval-secret"])
def test_other_agents_token_or_no_secret_is_refused(monkeypatch, secret):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AVAL_CALL_SECRET", raising=False)
    if secret:
        monkeypatch.setenv("AVAL_CALL_SECRET", secret)
    app = FastAPI()
    app.include_router(bridge.router)
    with TestClient(app).websocket_connect("/voice/aval-stream") as ws:
        ws.send_json({"event": "start", "start": {"customParameters": {
            "token": sign_call_token({**GOOD, "companyId": 1008, "clientId": 5}, "collections-secret")}}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_separate_agent_mounted_in_main():
    import main
    assert bridge._runner.agent.name == "aval_voice_agent"
    with TestClient(main.app).websocket_connect("/voice/aval-stream") as ws:
        ws.send_json({"event": "start", "start": {}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
