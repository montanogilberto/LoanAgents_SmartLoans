"""
Token metering — usage accumulation, the best-effort post to the backend, the
MeteredRunner wrapper and the middleware that identifies the company.
Never calls Gemini or the real backend.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.adk.runners import Runner

import metering
from metering import MeteredRunner, UsageMeter


def _event(prompt=0, candidates=0, thoughts=0):
    return SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=prompt, candidates_token_count=candidates, thoughts_token_count=thoughts))


def _meter(company_id=1):
    return UsageMeter(company_id, "/expenses/extract-ticket", "ticket_extraction_agent", "gemini-2.5-flash")


def test_meter_sums_usage_across_events_and_ignores_events_without_it():
    m = _meter()
    for e in (_event(3500, 600, 221), _event(100, 20, 0), SimpleNamespace(usage_metadata=None), SimpleNamespace()):
        m.add(e)
    assert (m.input_tokens, m.output_tokens, m.thoughts_tokens, m.total) == (3600, 620, 221, 4441)


def test_meter_ignores_negative_or_missing_counts():
    m = _meter()
    m.add(SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=-5, candidates_token_count=None, thoughts_token_count=7)))
    assert m.total == 7


def test_payload_matches_the_backend_contract():
    m = _meter(5)
    m.add(_event(10, 2, 1))
    assert m.payload() == {"tokens": [{
        "companyId": 5, "agentName": "ticket_extraction_agent", "endpointName": "/expenses/extract-ticket",
        "model": "gemini-2.5-flash", "inputTokens": 10, "outputTokens": 2, "thoughtsTokens": 1,
    }]}


def _flush(meter, key="secret"):
    client = mock.MagicMock()
    client.__aenter__.return_value = client
    client.post = mock.AsyncMock(return_value=mock.MagicMock(raise_for_status=mock.MagicMock()))
    with mock.patch.object(metering, "WORKER_KEY", key), \
         mock.patch.object(metering.httpx, "AsyncClient", return_value=client):
        asyncio.run(meter.flush())
    return client


def test_flush_posts_usage_with_the_worker_key():
    m = _meter(); m.add(_event(100, 10))
    client = _flush(m)
    url = client.post.call_args.args[0]
    assert url.endswith("/company_tokens/record")
    assert client.post.call_args.kwargs["headers"] == {"X-Worker-Key": "secret"}
    assert client.post.call_args.kwargs["json"]["tokens"][0]["inputTokens"] == 100


def test_flush_skips_when_nothing_to_bill():
    no_company = _meter(None); no_company.add(_event(100))
    assert _flush(no_company).post.call_count == 0
    no_usage = _meter()
    assert _flush(no_usage).post.call_count == 0


def test_flush_skips_without_a_worker_key():
    m = _meter(); m.add(_event(100))
    assert _flush(m, key="").post.call_count == 0


def test_flush_never_raises_when_the_backend_is_down():
    m = _meter(); m.add(_event(100))
    client = mock.MagicMock()
    client.__aenter__.return_value = client
    client.post = mock.AsyncMock(side_effect=RuntimeError("backend down"))
    with mock.patch.object(metering, "WORKER_KEY", "secret"), \
         mock.patch.object(metering.httpx, "AsyncClient", return_value=client):
        asyncio.run(m.flush())  # must not raise


def _run_runner(events, company_id=1):
    runner = MeteredRunner.__new__(MeteredRunner)
    runner.agent = SimpleNamespace(name="risk_agent", model="gemini-2.5-flash")

    async def fake_run_async(self, *a, **k):
        for e in events:
            yield e

    async def go():
        token = metering._company_id.set(company_id)
        try:
            return [e async for e in runner.run_async(user_id="u", session_id="s", new_message=None)]
        finally:
            metering._company_id.reset(token)

    flushed = []

    async def fake_flush(self):
        flushed.append(self.payload()["tokens"][0])

    with mock.patch.object(Runner, "run_async", fake_run_async), mock.patch.object(UsageMeter, "flush", fake_flush):
        out = asyncio.run(go())
    return out, flushed


def test_runner_passes_events_through_and_bills_the_total_once():
    events = [_event(1000, 100, 50), _event(500, 40, 0)]
    out, flushed = _run_runner(events)
    assert out == events
    assert flushed == [{
        "companyId": 1, "agentName": "risk_agent", "endpointName": "", "model": "gemini-2.5-flash",
        "inputTokens": 1500, "outputTokens": 140, "thoughtsTokens": 50,
    }]


def test_runner_still_flushes_when_the_run_fails_midway():
    runner = MeteredRunner.__new__(MeteredRunner)
    runner.agent = SimpleNamespace(name="risk_agent", model="gemini-2.5-flash")

    async def boom(self, *a, **k):
        yield _event(200, 20)
        raise RuntimeError("model error")

    flushed = []

    async def fake_flush(self):
        flushed.append(self.total)

    async def go():
        token = metering._company_id.set(1)
        try:
            async for _ in runner.run_async(user_id="u", session_id="s", new_message=None):
                pass
        finally:
            metering._company_id.reset(token)

    with mock.patch.object(Runner, "run_async", boom), mock.patch.object(UsageMeter, "flush", fake_flush):
        try:
            asyncio.run(go())
        except RuntimeError:
            pass
    assert flushed == [220]


def _app():
    app = FastAPI()
    app.middleware("http")(metering.metering_middleware)
    seen = {}

    @app.post("/support/pos-income")
    async def endpoint(body: dict):
        seen["company"] = metering._company_id.get()
        seen["endpoint"] = metering._endpoint.get()
        return {"ok": True}

    return TestClient(app), seen


def test_middleware_exposes_company_and_endpoint_to_the_endpoint_body():
    client, seen = _app()
    assert client.post("/support/pos-income", json={"companyId": 7, "message": "hola"}).status_code == 200
    assert seen == {"company": 7, "endpoint": "/support/pos-income"}


def test_middleware_ignores_a_missing_or_non_integer_company():
    client, seen = _app()
    client.post("/support/pos-income", json={"message": "hola"})
    assert seen["company"] is None
    client.post("/support/pos-income", json={"companyId": "7"})
    assert seen["company"] is None
    client.post("/support/pos-income", json={"companyId": True})
    assert seen["company"] is None


def test_middleware_context_does_not_leak_between_requests():
    client, seen = _app()
    client.post("/support/pos-income", json={"companyId": 7})
    assert metering._company_id.get() is None
