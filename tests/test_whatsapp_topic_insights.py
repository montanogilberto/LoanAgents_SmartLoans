"""WhatsApp topic tag + insights summary (runners mocked, no LLM call)."""
import asyncio
from types import SimpleNamespace

from google.genai.types import Content, Part


def _final(text):
    return SimpleNamespace(is_final_response=lambda: True,
                           content=Content(role="model", parts=[Part(text=text)]),
                           actions=None)


def _fake_runner(monkeypatch, runner, text, seen=None):
    async def run_async(*, user_id, session_id, new_message):
        if seen is not None:
            seen["payload"] = new_message.parts[0].text
        yield _final(text)
    monkeypatch.setattr(runner, "run_async", run_async)


def test_topic_agents_importable_and_routes_registered():
    import main
    from agents import whatsapp_insights_agent, whatsapp_topic_agent
    assert whatsapp_topic_agent.tools == [] and whatsapp_insights_agent.tools == []
    paths = {r.path for r in main.app.routes}
    assert "/support/whatsapp-insights" in paths


def test_topic_list_matches_prompt():
    from agents.whatsapp_topic.prompt import INSTRUCTION, TOPICS
    for topic in TOPICS:
        assert f"- {topic}:" in INSTRUCTION


def test_classify_returns_known_topic_and_passes_previous_reply(monkeypatch):
    import main
    seen = {}
    _fake_runner(monkeypatch, main._whatsapp_topic_runner, '```json\n{"topic": "horarios"}\n```', seen)
    topic = asyncio.run(main._classify_whatsapp_topic("3", "1. Precios\n3. Horarios"))
    assert topic == "horarios"
    assert "3. Horarios" in seen["payload"]


def test_classify_drops_unknown_topic(monkeypatch):
    import main
    _fake_runner(monkeypatch, main._whatsapp_topic_runner, '{"topic": "chisme"}')
    assert asyncio.run(main._classify_whatsapp_topic("hola", None)) is None


def test_classify_never_raises(monkeypatch):
    import main

    async def boom(**_kw):
        raise RuntimeError("gemini down")
        yield
    monkeypatch.setattr(main._whatsapp_topic_runner, "run_async", boom)
    assert asyncio.run(main._classify_whatsapp_topic("hola", None)) is None


def test_one_shot_sessions_are_deleted(monkeypatch):
    import main
    _fake_runner(monkeypatch, main._whatsapp_topic_runner, '{"topic": "precios"}')
    asyncio.run(main._classify_whatsapp_topic("cuánto cuesta", None))
    for _ in range(3):
        asyncio.run(main._classify_whatsapp_topic("cuánto cuesta", None))
    users = main._session_service.sessions.get("loan_agents_whatsapp_topic", {})
    assert list(users) == ["loan_agents_whatsapp_topic"]
    assert not users["loan_agents_whatsapp_topic"]


def test_reservations_route_returns_topic(monkeypatch):
    import main

    async def run_async(*, user_id, session_id, new_message):
        yield _final("¡Hola! Abrimos de 8 a 8.")

    async def fake_topic(message, previous_reply):
        return "horarios"
    monkeypatch.setattr(main._whatsapp_reservations_runner, "run_async", run_async)
    monkeypatch.setattr(main, "_classify_whatsapp_topic", fake_topic)
    req = main.WhatsAppReservationsRequest(companyId=1, phone="+520000000077", message="a qué hora abren")
    resp = asyncio.run(main.support_whatsapp_reservations(req))
    assert resp.topic == "horarios" and resp.reply


def test_insights_empty_period_skips_the_llm(monkeypatch):
    import main

    async def must_not_run(**_kw):
        raise AssertionError("LLM called")
        yield
    monkeypatch.setattr(main._whatsapp_insights_runner, "run_async", must_not_run)
    req = main.WhatsAppInsightsRequest(companyId=1, startDate="2026-10-01", endDate="2026-10-31", messages=["  "])
    resp = asyncio.run(main.support_whatsapp_insights(req))
    assert resp.messagesAnalyzed == 0 and resp.summary


def test_insights_parses_and_bounds_output(monkeypatch):
    import main
    seen = {}
    doubts = ",".join(f'{{"question": "q{i}", "count": {i}, "suggestion": "s"}}' for i in range(8))
    _fake_runner(monkeypatch, main._whatsapp_insights_runner,
                 f'{{"summary": "Preguntan precios.", "commonDoubts": [{doubts}, {{"count": 1}}], "unmetNeeds": ["tintorería"]}}',
                 seen)
    req = main.WhatsAppInsightsRequest(
        companyId=1, startDate="2026-10-01", endDate="2026-10-31",
        topicCounts=[{"topic": "precios", "count": 12}], messages=["x" * 1000, "precio?"])
    resp = asyncio.run(main.support_whatsapp_insights(req))
    assert resp.summary == "Preguntan precios."
    assert len(resp.commonDoubts) == 5
    assert resp.unmetNeeds == ["tintorería"]
    assert resp.messagesAnalyzed == 2
    assert "x" * 301 not in seen["payload"]
