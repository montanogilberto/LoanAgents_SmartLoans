"""Signup service finder (runner mocked, no LLM call)."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
from google.genai.types import Content, Part


def _final(text):
    return SimpleNamespace(is_final_response=lambda: True, partial=False,
                           content=Content(role="model", parts=[Part(text=text)]),
                           actions=None)


def _fake_runner(monkeypatch, text, seen=None):
    import main

    async def run_async(*, user_id, session_id, new_message, **_):
        if seen is not None:
            seen["payload"] = new_message.parts[0].text
        yield _final(text)
    monkeypatch.setattr(main._service_finder_runner, "run_async", run_async)
    main._finder_calls.clear()


def _post(conversation):
    import main
    return TestClient(main.app).post("/signup/service-finder", json={"conversation": conversation})


def test_agent_has_no_tools_and_route_registered():
    import main
    from agents import service_finder_agent
    assert service_finder_agent.tools == []
    assert "/signup/service-finder" in {r.path for r in main.app.routes}


def test_catalog_ids_match_frontend_ids():
    from agents.service_finder.catalog import SERVICE_IDS
    assert SERVICE_IDS == ("pos", "smartloans", "factory", "arcade")


def test_recommend_passes_catalog_and_filters_alternatives(monkeypatch):
    seen = {}
    _fake_runner(monkeypatch, '```json\n{"status":"recommend","service":"pos","alternatives":["pos","casino","factory"],"reply":"Te sirve POS."}\n```', seen)
    r = _post([{"role": "visitor", "text": "tengo una tienda y quiero cobrar"}])
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "recommend" and body["service"] == "pos"
    assert body["alternatives"] == ["factory"]
    assert '"smartloans"' in seen["payload"] and '"questionsLeft": 2' in seen["payload"]


def test_unknown_service_becomes_unsure(monkeypatch):
    _fake_runner(monkeypatch, '{"status":"recommend","service":"casino","reply":"x"}')
    body = _post([{"role": "visitor", "text": "algo"}]).json()
    assert body["status"] == "unsure" and body["service"] is None


def test_ask_is_dropped_when_no_questions_left(monkeypatch):
    _fake_runner(monkeypatch, '{"status":"ask","reply":"¿Tienes negocio?","options":["Sí","No"]}')
    convo = [
        {"role": "visitor", "text": "hola"}, {"role": "assistant", "text": "¿Qué buscas?"},
        {"role": "visitor", "text": "no sé"}, {"role": "assistant", "text": "¿Negocio o personal?"},
        {"role": "visitor", "text": "ambos"},
    ]
    assert _post(convo).json()["status"] == "unsure"


def test_ask_with_options_passes_through(monkeypatch):
    _fake_runner(monkeypatch, '{"status":"ask","reply":"¿Para tu negocio o para ti?","options":["Para mi negocio","Para mí"]}')
    body = _post([{"role": "visitor", "text": "quiero dinero"}]).json()
    assert body["status"] == "ask" and body["options"] == ["Para mi negocio", "Para mí"]


def test_last_turn_must_be_visitor():
    import main
    main._finder_calls.clear()
    assert _post([{"role": "assistant", "text": "hola"}]).status_code == 400


def test_runner_failure_is_502(monkeypatch):
    import main

    async def boom(**_kw):
        raise RuntimeError("gemini down")
        yield
    monkeypatch.setattr(main._service_finder_runner, "run_async", boom)
    main._finder_calls.clear()
    assert _post([{"role": "visitor", "text": "hola"}]).status_code == 502
