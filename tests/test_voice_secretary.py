"""
Voice secretary: audio codec, signed call token, PICKUP_AGREEMENT contract,
the agent's tools, and the Twilio bridge driven with a fake live runner (no
Gemini, no Twilio) — barge-in, proposals, and hang-up after the goodbye.
"""
import asyncio
import base64
import json
import time
import sys
import warnings
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from retrieval.contracts import CONTRACTS
from voice import twilio_bridge
from voice.audio import Downsampler24kTo8k, pcm16_to_ulaw, ulaw8k_to_pcm16k
from voice.call_token import InvalidCallToken, sign_call_token, verify_call_token

SECRET = "test-secret"
PAYLOAD = {"companyId": 1, "branchId": 2, "clientId": 77, "orderId": 900,
           "companyName": "Negocio", "customerName": "Ana", "order": {"service": "Lavado", "total": 120}}


# ── audio ──────────────────────────────────────────────────────────────
def _audioop():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            import audioop
        except ImportError:
            return None
    return audioop


def test_ulaw_codec_matches_audioop():
    audioop = _audioop()
    if audioop is None:
        pytest.skip("audioop not available on this Python")
    ulaw = bytes(range(256))
    expected = audioop.ulaw2lin(ulaw, 2)
    # Every 2nd sample of the 16 kHz output is the decoded 8 kHz sample.
    decoded = ulaw8k_to_pcm16k(ulaw)
    assert b"".join(decoded[4 * i + 2:4 * i + 4] for i in range(256)) == expected
    # audioop truncates to 14 bits first, so a few values at segment edges land
    # one code apart — inaudible, but the codes may differ by at most one step.
    pcm = b"".join(int(v).to_bytes(2, "little", signed=True) for v in range(-32768, 32768))
    ours, ref = pcm16_to_ulaw(pcm), audioop.lin2ulaw(pcm, 2)
    diffs = [abs(a - b) for a, b in zip(ours, ref) if a != b]
    assert max(diffs, default=0) == 1 and len(diffs) < len(ref) * 0.01


def test_ulaw_roundtrip_is_close():
    pcm = b"".join(int(v).to_bytes(2, "little", signed=True) for v in (0, 1000, -1000, 20000, -20000))
    back = ulaw8k_to_pcm16k(pcm16_to_ulaw(pcm))
    samples = [int.from_bytes(back[i:i + 2], "little", signed=True) for i in range(2, len(back), 4)]
    for want, got in zip((0, 1000, -1000, 20000, -20000), samples):
        assert abs(want - got) <= max(64, abs(want) * 0.04)


def test_upsampled_length_doubles():
    assert len(ulaw8k_to_pcm16k(b"\xff" * 160)) == 160 * 2 * 2   # 2x samples, 2 bytes each


def test_downsampler_carries_remainder_across_chunks():
    pcm = b"".join(int(1000).to_bytes(2, "little", signed=True) for _ in range(10))
    ds = Downsampler24kTo8k()
    out = ds.convert(pcm[:7]) + ds.convert(pcm[7:])     # odd byte split + 10 samples
    assert len(out) == 3                                  # 9 usable samples → 3, one carried
    ds.reset()
    assert ds.convert(pcm[:4]) == b""                     # 2 samples: nothing yet


# ── token ──────────────────────────────────────────────────────────────
def test_call_token_roundtrip():
    token = sign_call_token(PAYLOAD, SECRET, now=1000)
    payload = verify_call_token(token, SECRET, now=1001)
    assert payload["companyId"] == 1 and payload["clientId"] == 77 and payload["exp"] == 1600


@pytest.mark.parametrize("mutate", [
    lambda t: t[:-2] + ("A" if t[-2] != "A" else "B") + t[-1],     # signature
    lambda t: "x" + t,                                              # body
    lambda t: t.replace(".", ""),                                   # format
])
def test_call_token_rejects_tampering(mutate):
    token = sign_call_token(PAYLOAD, SECRET, now=1000)
    with pytest.raises(InvalidCallToken):
        verify_call_token(mutate(token), SECRET, now=1001)


def test_call_token_rejects_expired_wrong_secret_and_missing_ids():
    token = sign_call_token(PAYLOAD, SECRET, ttl_s=60, now=1000)
    with pytest.raises(InvalidCallToken):
        verify_call_token(token, SECRET, now=1061)
    with pytest.raises(InvalidCallToken):
        verify_call_token(token, "other", now=1001)
    with pytest.raises(InvalidCallToken):
        verify_call_token(sign_call_token({"companyId": 1}, SECRET, now=1000), SECRET, now=1001)
    with pytest.raises(InvalidCallToken):
        verify_call_token(token, "", now=1001)


def test_call_token_ttl_is_capped():
    payload = verify_call_token(sign_call_token(PAYLOAD, SECRET, ttl_s=10 ** 6, now=0), SECRET, now=1)
    assert payload["exp"] == 15 * 60


# ── contract + agent ───────────────────────────────────────────────────
def test_pickup_agreement_contract():
    c = CONTRACTS["PICKUP_AGREEMENT"]
    ok = {"deliveryMode": "pickup", "weekday": "lunes", "date": "2026-11-09", "time": "18:00"}
    assert c.validate(ok) == []
    assert any("weekday" in e for e in c.validate({k: v for k, v in ok.items() if k != "weekday"}))
    assert any("address" in e for e in c.validate({**ok, "deliveryMode": "delivery"}))
    assert c.validate({**ok, "deliveryMode": "delivery", "time": "09:30", "address": "Calle 1"}) == []
    assert c.validate({**ok, "date": "mañana", "time": "25:00"})
    assert c.validate({**ok, "companyId": 5})


def test_voice_agent_tools_and_model():
    from agents.voice_secretary.agent import voice_secretary_agent
    names = {t.func.__name__ for t in voice_secretary_agent.tools if hasattr(t, "func")}
    assert names == {"get_faqs", "propose_action", "confirm_customer_identity", "end_call"}   # info + hours come in the call context
    model = voice_secretary_agent.model.model
    assert "live" in model or "native-audio" in model


def test_trusted_ids_override_model_args():
    from agents.voice_secretary.agent import _use_trusted_ids
    from tools.backend_api import get_business_info
    args = {"company_id": 999, "branch_id": 999}
    ctx = SimpleNamespace(state={"voice_company_id": 1, "voice_branch_id": 2})
    _use_trusted_ids(SimpleNamespace(func=get_business_info), args, ctx)
    assert args == {"company_id": 1, "branch_id": 2}


def test_end_call_sets_flag():
    from agents.voice_secretary.agent import end_call
    ctx = SimpleNamespace(state={})
    assert end_call(ctx) == {"ending": True} and ctx.state["voice_end_call"] is True


def test_prompt_is_business_agnostic():
    from agents.voice_secretary.prompt import INSTRUCTION
    assert "Lavander" not in INSTRUCTION and "$" not in INSTRUCTION


def test_call_context_uses_hermosillo_dates():
    ctx = twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 18, 5))
    assert ctx["today"] == "2026-11-07" and ctx["nowTime"] == "18:05"
    assert ctx["nextDays"][0] == {"date": "2026-11-07", "weekday": "sábado"}
    assert ctx["nextDays"][2]["weekday"] == "lunes"
    assert "order" not in ctx and "120" not in json.dumps(ctx)   # only after identity is confirmed
    assert "companyId" not in ctx and "clientId" not in ctx


# ── bridge with fakes ──────────────────────────────────────────────────
class FakeWS:
    """Plays Twilio: feeds frames, records what the bridge sends, echoes marks."""

    def __init__(self, frames):
        self.incoming = asyncio.Queue()
        for f in frames:
            self.incoming.put_nowait(json.dumps(f))
        self.sent = []

    async def receive_text(self):
        return await self.incoming.get()

    async def send_text(self, text):
        frame = json.loads(text)
        self.sent.append(frame)
        if frame["event"] == "mark":     # Twilio echoes a mark once audio before it has played
            self.incoming.put_nowait(json.dumps({"event": "mark", "mark": frame["mark"]}))


def _ev(**kw):
    base = dict(interrupted=None, input_transcription=None, output_transcription=None, partial=None,
                content=None, actions=SimpleNamespace(state_delta={}), turn_complete=None, usage_metadata=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _audio(n_samples=300):
    blob = SimpleNamespace(data=b"\x00\x10" * n_samples, mime_type="audio/pcm;rate=24000")
    return SimpleNamespace(parts=[SimpleNamespace(inline_data=blob)])


class FakeRunner:
    def __init__(self, events):
        self.events = events
        self.queue = None

    async def run_live(self, *, user_id, session_id, live_request_queue, run_config):
        self.queue = live_request_queue
        for e in self.events:
            await asyncio.sleep(0)
            yield e
        await asyncio.sleep(3600)   # a live session stays open until the call ends


def _t(text):
    return SimpleNamespace(text=text)


@pytest.fixture(autouse=True)
def _no_backend(monkeypatch):
    """Bridge tests never reach the real backend."""
    monkeypatch.setattr(twilio_bridge, "get_business_info", lambda c, b=None: {"address": "Calle 1", "phone": None})
    monkeypatch.setattr(twilio_bridge, "get_reservation_hours",
                        lambda c: [{"dayOfWeek": 0, "openTime": "10:00", "closeTime": "22:00"},
                                   {"dayOfWeek": 5, "openTime": "10:00", "closeTime": "17:00"}])


def test_business_facts_are_read_once_and_name_the_weekdays():
    facts = asyncio.run(twilio_bridge.fetch_business_facts(1, None))
    assert facts["business"]["address"] == "Calle 1"
    assert facts["openingHours"][0] == {"weekday": "lunes", "open": "10:00", "close": "22:00"}
    assert facts["openingHours"][6] == {"weekday": "domingo", "closed": True}


def test_business_facts_unavailable_when_backend_fails(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("slow")
    monkeypatch.setattr(twilio_bridge, "get_business_info", boom)
    monkeypatch.setattr(twilio_bridge, "get_reservation_hours", boom)
    assert asyncio.run(twilio_bridge.fetch_business_facts(1, None)) == {
        "business": "unavailable", "openingHours": "unavailable"}


def test_bridge_full_call_flow():
    events = [
        _ev(output_transcription=_t("Hola, ¿hablo con Ana?"), partial=True),
        _ev(content=_audio()),
        _ev(turn_complete=True),
        _ev(input_transcription=_t("Sí, "), partial=True),
        _ev(input_transcription=_t("soy yo"), partial=True),
        _ev(output_transcription=_t("Su pedido"), partial=True),
        _ev(interrupted=True),                                   # barge-in
        _ev(input_transcription=_t("paso mañana a las seis"), partial=True),
        _ev(actions=SimpleNamespace(state_delta={"pending_action": {
            "capability": "PICKUP_AGREEMENT",
            "fields": {"deliveryMode": "pickup", "date": "2026-11-09", "time": "18:00"},
            "confirmationSummary": "x"}})),
        _ev(output_transcription=_t("Listo, hasta luego"), partial=True),
        _ev(actions=SimpleNamespace(state_delta={"voice_end_call": True})),
        _ev(content=_audio()),
        _ev(turn_complete=True),
    ]
    ws = FakeWS([{"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}])
    runner = FakeRunner(events)
    call = twilio_bridge.TwilioCall(ws, PAYLOAD, stream_sid="MZ1", call_sid="CA1", runner=runner)
    result = asyncio.run(asyncio.wait_for(call.run(), 5))

    kinds = [f["event"] for f in ws.sent]
    assert "clear" in kinds and kinds.count("media") == 2 and kinds[-1] == "mark"
    assert all(f["streamSid"] == "MZ1" for f in ws.sent)
    assert result.endedBy == "agent"
    assert [p["capability"] for p in result.proposals] == ["PICKUP_AGREEMENT"]
    assert result.transcript == [
        {"speaker": "agent", "text": "Hola, ¿hablo con Ana?"},
        {"speaker": "customer", "text": "Sí, soy yo"},
        {"speaker": "agent", "text": "Su pedido"},
        {"speaker": "customer", "text": "paso mañana a las seis"},
        {"speaker": "agent", "text": "Listo, hasta luego"},
    ]
    summary = json.dumps(result.safe_summary())
    assert "Ana" not in summary and "2026-11-09" not in summary   # no PII / field values in logs


def test_bridge_customer_hangup_ends_call():
    ws = FakeWS([{"event": "stop"}])
    call = twilio_bridge.TwilioCall(ws, PAYLOAD, stream_sid="MZ1", call_sid="CA2", runner=FakeRunner([]))
    result = asyncio.run(asyncio.wait_for(call.run(), 5))
    assert result.endedBy == "customer" and result.proposals == []


def test_stream_route_refuses_bad_token(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setenv("VOICE_CALL_SECRET", SECRET)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)   # signature off: this test is about the call token
    app = FastAPI()
    app.include_router(twilio_bridge.router)
    with TestClient(app).websocket_connect("/voice/twilio-stream") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps({"event": "start", "start": {
            "streamSid": "MZ", "callSid": "CA", "customParameters": {"token": "forged.token"}}}))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 1008


def test_bridge_drops_a_repeated_goodbye_after_end_call():
    events = [
        _ev(output_transcription=_t("Hasta luego"), partial=True),
        _ev(content=_audio()),
        _ev(actions=SimpleNamespace(state_delta={"voice_end_call": True})),
        _ev(output_transcription=_t("Hasta luego"), partial=True),   # the model says it again
        _ev(content=_audio()),
        _ev(turn_complete=True),
    ]
    ws = FakeWS([])
    call = twilio_bridge.TwilioCall(ws, PAYLOAD, stream_sid="MZ1", call_sid="CA3", runner=FakeRunner(events))
    result = asyncio.run(asyncio.wait_for(call.run(), 5))
    assert [f["event"] for f in ws.sent] == ["media", "mark"]
    assert result.transcript == [{"speaker": "agent", "text": "Hasta luego"}]
    assert result.endedBy == "agent"


def test_late_customer_transcription_does_not_split_the_agent_turn():
    events = [
        _ev(input_transcription=_t("Paso el lunes"), partial=True),
        _ev(output_transcription=_t("Perfecto, "), partial=True),
        _ev(input_transcription=_t(" a las seis"), partial=True),    # arrives while the agent talks
        _ev(output_transcription=_t("el lunes a las seis."), partial=True),
        _ev(turn_complete=True),
        _ev(actions=SimpleNamespace(state_delta={"voice_end_call": True})),
        _ev(turn_complete=True),
    ]
    seen = []
    twilio_bridge.TRANSCRIPT_HOOKS[:] = [lambda who, text: seen.append((who, text))]
    try:
        call = twilio_bridge.TwilioCall(FakeWS([]), PAYLOAD, stream_sid="MZ1", call_sid="CA4",
                                        runner=FakeRunner(events))
        result = asyncio.run(asyncio.wait_for(call.run(), 5))
    finally:
        twilio_bridge.TRANSCRIPT_HOOKS[:] = []
    assert ("agent", "Perfecto, el lunes a las seis.") in seen
    assert [t["speaker"] for t in result.transcript].count("agent") == 1


def test_silence_watchdog_nudges_then_asks_to_close(monkeypatch):
    monkeypatch.setattr(twilio_bridge, "SILENCE_NUDGE_S", 0)
    call = twilio_bridge.TwilioCall(FakeWS([]), PAYLOAD, stream_sid="MZ1", call_sid="CA5", runner=FakeRunner([]))
    sent = []
    monkeypatch.setattr(call.queue, "send_content", lambda content: sent.append(content.parts[0].text))

    async def go():
        task = asyncio.create_task(call._silence_watchdog())
        await asyncio.sleep(2.5)
        task.cancel()
    asyncio.run(go())
    assert "sigue en la línea" in sent[0] and "termina la llamada" in sent[1]
    assert "WhatsApp" not in sent[1]                    # nothing sends one yet


def test_goodbye_turn_before_end_call_still_mutes_the_repeat():
    """The model finishes its goodbye turn, THEN calls end_call and talks again."""
    events = [
        _ev(output_transcription=_t("Hasta luego"), partial=True),
        _ev(content=_audio()),
        _ev(turn_complete=True),
        _ev(actions=SimpleNamespace(state_delta={"voice_end_call": True})),
        _ev(output_transcription=_t("Hasta luego"), partial=True),
        _ev(content=_audio()),
        _ev(turn_complete=True),
    ]
    ws = FakeWS([])
    call = twilio_bridge.TwilioCall(ws, PAYLOAD, stream_sid="MZ1", call_sid="CA6", runner=FakeRunner(events))
    result = asyncio.run(asyncio.wait_for(call.run(), 5))
    assert [f["event"] for f in ws.sent] == ["media", "mark"]
    assert result.transcript == [{"speaker": "agent", "text": "Hasta luego"}]


def _loud_frame():
    from voice.audio import pcm16_to_ulaw
    return base64.b64encode(pcm16_to_ulaw(b"".join(
        (8000 if i % 2 else -8000).to_bytes(2, "little", signed=True) for i in range(160)))).decode()


def test_ulaw_energy_separates_silence_from_speech():
    from voice.audio import ulaw_energy
    assert ulaw_energy(b"\xff" * 160) < 50
    assert ulaw_energy(base64.b64decode(_loud_frame())) > 5000


def test_customer_talking_over_buffered_playback_clears_it():
    call = twilio_bridge.TwilioCall(FakeWS([]), PAYLOAD, stream_sid="MZ1", call_sid="CA7", runner=FakeRunner([]))
    call._playback_until = time.monotonic() + 5            # agent audio still playing at Twilio
    loud = base64.b64decode(_loud_frame())

    async def go():
        for _ in range(twilio_bridge.BARGE_IN_FRAMES + 3):
            await call._check_barge_in(loud)
    asyncio.run(go())
    assert [f["event"] for f in call.ws.sent] == ["clear"] and call.result.bargeIns == 1


def test_customer_talking_after_playback_is_not_a_barge_in():
    call = twilio_bridge.TwilioCall(FakeWS([]), PAYLOAD, stream_sid="MZ1", call_sid="CA8", runner=FakeRunner([]))
    loud = base64.b64decode(_loud_frame())

    async def go():
        for _ in range(10):
            await call._check_barge_in(loud)
    asyncio.run(go())
    assert call.ws.sent == [] and call.result.bargeIns == 0


def test_latest_agreement_wins_and_prompt_version_is_recorded():
    from agents.voice_secretary.prompt import PROMPT_VERSION
    r = twilio_bridge.CallResult(callSid="CA", companyId=1, clientId=2, orderId=3)
    r.proposals = [{"capability": "PICKUP_AGREEMENT", "fields": {"date": "2026-11-08"}},
                   {"capability": "REQUEST_HUMAN", "fields": {"reason": "x"}},
                   {"capability": "PICKUP_AGREEMENT", "fields": {"date": "2026-11-09"}}]
    assert r.agreement["fields"]["date"] == "2026-11-09"
    assert r.promptVersion == PROMPT_VERSION and r.safe_summary()["promptVersion"] == PROMPT_VERSION
    assert twilio_bridge.CallResult(callSid="CA", companyId=1, clientId=2, orderId=3).agreement is None


def test_order_is_only_revealed_by_confirm_customer_identity():
    from agents.voice_secretary.agent import confirm_customer_identity
    ctx = SimpleNamespace(state={"voice_order": twilio_bridge.order_details(PAYLOAD), "voice_customer_name": "Ana"})
    assert asyncio.run(confirm_customer_identity("Sí, soy yo", ctx)) == {
        "confirmed": True, "order": {"service": "Lavado", "total": 120}}
    assert ctx.state["voice_identity_confirmed"] is True


@pytest.mark.parametrize("said, ok", [
    ("Sí, soy yo", True), ("sí", True), ("Él habla", True), ("Ana, dígame", True), ("Mande", True),
    ("Así es", True), ("Ana", True),
    ("<noise>", False),                 # taken as a yes on the live call
    ("¿Bueno?", False), ("Bueno", False), ("", False),
    ("No, se equivocó de número", False), ("No, aquí no vive Ana", False),
])
def test_identity_needs_a_clear_yes(said, ok):
    from agents.voice_secretary.agent import confirm_customer_identity
    ctx = SimpleNamespace(state={"voice_order": {"service": "x", "total": 1}, "voice_customer_name": "Ana López"})
    out = asyncio.run(confirm_customer_identity(said, ctx))
    assert out["confirmed"] is ok
    assert ("order" in out) is ok and ctx.state.get("voice_identity_confirmed", False) is ok


_WEEK = [{"weekday": d, "open": "10:00", "close": "22:00"} for d in ("lunes", "martes", "jueves", "viernes")] + [
    {"weekday": "miércoles", "closed": True}, {"weekday": "sábado", "open": "10:00", "close": "17:00"},
    {"weekday": "domingo", "open": "10:00", "close": "14:00"}]


def test_available_days_drop_today_after_closing():
    """Live call at 22:31 on a Saturday offered "hoy" — the business closed at 17:00."""
    ctx = twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 22, 31), {"openingHours": _WEEK})
    days = ctx["availableDays"]
    assert days[0] == {"date": "2026-11-08", "weekday": "domingo", "label": "mañana", "open": "10:00", "close": "14:00"}
    assert "2026-11-07" not in [d["date"] for d in days]
    assert "miércoles" not in [d["weekday"] for d in days]           # closed day left out


def test_available_days_today_starts_now():
    ctx = twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 12, 5), {"openingHours": _WEEK})
    assert ctx["availableDays"][0] == {"date": "2026-11-07", "weekday": "sábado", "label": "hoy",
                                       "open": "12:15", "close": "17:00"}


def test_model_sees_available_days_not_the_raw_table():
    ctx = twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 12, 5), {"openingHours": _WEEK})
    seen = twilio_bridge.model_context(ctx)
    assert "availableDays" in seen and "openingHours" not in seen and "nextDays" not in seen
    assert twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 12, 5), {})["availableDays"] == "unavailable"


def test_prompt_keeps_time_ranges_in_notes():
    from agents.voice_secretary.prompt import INSTRUCTION
    assert "range goes in notes" in INSTRUCTION


def _signed(url, token):
    import hashlib
    import hmac as _hmac
    return base64.b64encode(_hmac.new(token.encode(), url.encode(), hashlib.sha1).digest()).decode()


def test_twilio_signature_check():
    url = "wss://abc.ngrok-free.app/voice/twilio-stream"
    assert twilio_bridge.twilio_signature_ok(url, _signed(url, "tok"), "tok")
    assert not twilio_bridge.twilio_signature_ok(url, _signed(url, "other"), "tok")
    assert not twilio_bridge.twilio_signature_ok(url + "?x=1", _signed(url, "tok"), "tok")
    assert not twilio_bridge.twilio_signature_ok(url, None, "tok")


def _route_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("VOICE_CALL_SECRET", SECRET)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    app = FastAPI()
    app.include_router(twilio_bridge.router)
    return TestClient(app)


def test_stream_route_rejects_a_missing_twilio_signature(monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc:
        with _route_client(monkeypatch).websocket_connect("/voice/twilio-stream"):
            pass
    assert exc.value.code == 1008


def test_stream_route_accepts_a_valid_twilio_signature(monkeypatch):
    """Signature OK → the socket is accepted and the call token is checked next."""
    from starlette.websockets import WebSocketDisconnect
    url = "wss://testserver/voice/twilio-stream"
    with _route_client(monkeypatch).websocket_connect(
            "/voice/twilio-stream", headers={"X-Twilio-Signature": _signed(url, "tok")}) as ws:
        ws.send_text(json.dumps({"event": "start", "start": {"customParameters": {"token": "forged.token"}}}))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 1008      # accepted, then refused for the forged call token


# ── calendar check (the model mapped weekdays to wrong dates on live calls) ──
def _calendar():
    from agents.voice_secretary.calendar_check import build_calendar
    ctx = twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 12, 30), {
        "business": {}, "openingHours": [
            {"weekday": "lunes", "open": "10:00", "close": "22:00"},
            {"weekday": "martes", "open": "10:00", "close": "22:00"},
            {"weekday": "miércoles", "closed": True},
            {"weekday": "sábado", "open": "10:00", "close": "17:00"},
            {"weekday": "domingo", "open": "10:00", "close": "14:00"}]})
    return build_calendar(ctx)


@pytest.mark.parametrize("fields, error", [
    ({"weekday": "lunes", "date": "2026-11-09", "time": "18:00"}, None),
    ({"weekday": "lunes", "date": "2026-11-10", "time": "18:00"}, "the date is 2026-11-09"),   # seen live
    ({"weekday": "lunes", "date": "2026-11-07", "time": "18:00"}, "the date is 2026-11-09"),   # seen live
    ({"weekday": "sábado", "date": "2026-11-07", "time": "18:00"}, "outside sábado"),
    ({"weekday": "domingo", "date": "2026-11-08", "time": "14:00"}, "outside domingo"),        # closing time
    ({"weekday": "miércoles", "date": "2026-11-11", "time": "12:00"}, "closed on miércoles"),
    ({"weekday": "sábado", "date": "2026-11-07", "time": "12:00"}, "already passed"),
    ({"weekday": "sábado", "date": "2026-11-07", "time": "13:00"}, None),
    ({"weekday": "lunes", "date": "2026-12-01", "time": "12:00"}, "outside the next days"),
    ({"date": "2026-11-09", "time": "18:00"}, "weekday is required"),
])
def test_agreement_slot_check(fields, error):
    from agents.voice_secretary.calendar_check import check_agreement_slot
    errors = check_agreement_slot(fields, _calendar())
    assert (errors == []) if error is None else any(error in e for e in errors), errors


def test_voice_propose_action_refuses_a_wrong_date_and_accepts_the_fix():
    from agents.voice_secretary.agent import propose_action
    ctx = SimpleNamespace(state={"voice_calendar": _calendar()})
    bad = {"deliveryMode": "pickup", "weekday": "lunes", "date": "2026-11-10", "time": "18:00"}
    out = propose_action("PICKUP_AGREEMENT", bad, "x", ctx)
    assert out["proposed"] is False and "pending_action" not in ctx.state
    out = propose_action("PICKUP_AGREEMENT", {**bad, "date": "2026-11-09"}, "x", ctx)
    assert out == {"proposed": True} and ctx.state["pending_action"]["fields"]["date"] == "2026-11-09"
    assert propose_action("REQUEST_HUMAN", {"reason": "queja"}, "x", ctx) == {"proposed": True}



def test_line_level_learns_a_quiet_line():
    from voice.audio import LineLevel
    lvl = LineLevel(ceiling=900)
    for _ in range(300):
        lvl.is_speech(60)                     # quiet landline background
    assert lvl.threshold == LineLevel.MIN_SPEECH
    assert lvl.is_speech(400)                 # soft speech a fixed 900 would miss
    stats = lvl.stats()
    assert stats["frames"] == 301 and stats["speechFrames"] == 1 and stats["p50"] == 60


def test_line_level_caps_threshold_on_a_noisy_line():
    from voice.audio import LineLevel
    lvl = LineLevel(ceiling=900)
    for _ in range(500):
        lvl.is_speech(400)                    # noisy background below the ceiling
    assert lvl.threshold <= 900 and not lvl.is_speech(350)


def test_live_config_uses_sensitive_speech_start():
    from google.genai import types
    cfg = twilio_bridge.live_run_config().realtime_input_config.automatic_activity_detection
    assert cfg.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_HIGH



def test_walk_in_client_is_never_called():
    """Client 1 is the POS walk-in placeholder; it has a real phone on file."""
    token = sign_call_token({**PAYLOAD, "clientId": 1}, SECRET, now=1000)
    with pytest.raises(InvalidCallToken, match="walk-in"):
        verify_call_token(token, SECRET, now=1001)



# ── v1.5: live evidence, delivery, lifecycle, hours ──────────────────────
def _live_ctx(session_id, state=None):
    inv = SimpleNamespace(session=SimpleNamespace(id=session_id))
    return SimpleNamespace(state=state or {}, _invocation_context=inv)


@pytest.fixture
def live_call():
    from agents.voice_secretary import live_state
    live_state.register("S1")
    yield live_state
    live_state.unregister("S1")


def test_live_identity_ignores_the_models_claim_when_the_line_heard_noise(live_call):
    """Real call 2026-11-07: transcript "<noise>", model passed "sí"."""
    from agents.voice_secretary.agent import confirm_customer_identity
    live_call.add_customer_text("S1", "<noise>")
    ctx = _live_ctx("S1", {"voice_order": {"service": "x"}, "voice_customer_name": "Gilbertito"})
    out = asyncio.run(confirm_customer_identity("Sí, soy yo", ctx))
    assert out["confirmed"] is False and "order" not in out


def test_live_identity_accepts_what_the_line_heard(live_call):
    from agents.voice_secretary.agent import confirm_customer_identity
    live_call.add_customer_text("S1", "Sí, ")
    live_call.add_customer_text("S1", "soy yo")
    ctx = _live_ctx("S1", {"voice_order": {"service": "x"}, "voice_customer_name": "Gilbertito"})
    assert asyncio.run(confirm_customer_identity("whatever", ctx))["confirmed"] is True


def test_delivery_is_refused_when_not_allowed():
    from agents.voice_secretary.agent import propose_action
    fields = {"deliveryMode": "delivery", "weekday": "lunes", "date": "2026-11-09", "time": "13:00", "address": "Calle 1"}
    ctx = SimpleNamespace(state={"voice_calendar": _calendar(), "voice_delivery_allowed": False})
    out = propose_action("PICKUP_AGREEMENT", fields, "x", ctx)
    assert out["proposed"] is False and any("delivery" in e for e in out["errors"])
    ctx = SimpleNamespace(state={"voice_calendar": _calendar(), "voice_delivery_allowed": True})
    assert propose_action("PICKUP_AGREEMENT", fields, "x", ctx) == {"proposed": True}


def test_end_call_asks_anything_else_once_after_an_agreement(live_call):
    from agents.voice_secretary.agent import end_call, propose_action
    ctx = _live_ctx("S1", {"voice_calendar": _calendar()})
    ok = {"deliveryMode": "pickup", "weekday": "lunes", "date": "2026-11-09", "time": "18:00"}
    assert propose_action("PICKUP_AGREEMENT", ok, "x", ctx)["proposed"] is True
    first = end_call(ctx)
    assert first["ending"] is False and "algo más" in first["error"] and "voice_end_call" not in ctx.state
    assert end_call(ctx) == {"ending": True}          # only once — never a loop


def test_end_call_goes_through_when_the_customer_answered(live_call):
    from agents.voice_secretary.agent import end_call, propose_action
    ctx = _live_ctx("S1", {"voice_calendar": _calendar()})
    ok = {"deliveryMode": "pickup", "weekday": "lunes", "date": "2026-11-09", "time": "18:00"}
    propose_action("PICKUP_AGREEMENT", ok, "x", ctx)
    time.sleep(0.01)
    live_call.add_customer_text("S1", "No, es todo, gracias")
    assert end_call(ctx) == {"ending": True}


def test_spoken_hours_group_only_identical_consecutive_days():
    """Live call said "lunes a sábado de 10 a 10" — Saturday closes at 17:00."""
    assert twilio_bridge.spoken_hours(_WEEK) == [
        "De lunes a martes: de las 10 de la mañana a las 10 de la noche",
        "Miércoles: cerrado",
        "De jueves a viernes: de las 10 de la mañana a las 10 de la noche",
        "Sábado: de las 10 de la mañana a las 5 de la tarde",
        "Domingo: de las 10 de la mañana a las 2 de la tarde"]
    assert twilio_bridge.spoken_hours(None) == "unavailable"


def test_context_flags_delivery_and_payment_methods():
    reg = {"business": {"deliveryPolicy": "Entregamos L-V", "paymentMethods": "NO REGISTRADO — x"},
           "openingHours": _WEEK}
    ctx = twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 12, 0), reg)
    assert ctx["deliveryAllowed"] is True and ctx["paymentMethodsRegistered"] is False
    bare = twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 12, 0), {"business": "unavailable"})
    assert bare["deliveryAllowed"] is False


def test_agreement_revisions_are_numbered_and_repeats_counted():
    events = [
        _ev(output_transcription=_t("¿A qué hora pasaría?"), partial=True), _ev(turn_complete=True),
        _ev(output_transcription=_t("¿A qué hora pasaría?"), partial=True), _ev(turn_complete=True),
        _ev(actions=SimpleNamespace(state_delta={"pending_action": {"capability": "PICKUP_AGREEMENT", "fields": {"date": "a"}}})),
        _ev(actions=SimpleNamespace(state_delta={"pending_action": {"capability": "PICKUP_AGREEMENT", "fields": {"date": "b"}}})),
        _ev(actions=SimpleNamespace(state_delta={"voice_end_call": True})), _ev(turn_complete=True),
    ]
    call = twilio_bridge.TwilioCall(FakeWS([]), PAYLOAD, stream_sid="MZ1", call_sid="CA9", runner=FakeRunner(events))
    result = asyncio.run(asyncio.wait_for(call.run(), 5))
    assert [p["revision"] for p in result.proposals] == [1, 2]
    assert result.agreement["fields"]["date"] == "b" and result.repeatedQuestions == 1



def test_no_whatsapp_is_promised_until_the_backend_sends_it(monkeypatch):
    """Customers were told "le va a llegar un WhatsApp" — nothing sends it yet."""
    from agents.voice_secretary.prompt import INSTRUCTION
    monkeypatch.delenv("VOICE_WHATSAPP_CONFIRMATION", raising=False)
    ctx = twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 12, 0), {})
    assert "WhatsApp" not in ctx["followUp"] and "equipo" in ctx["followUp"]
    assert "WhatsApp con el resumen" not in INSTRUCTION
    monkeypatch.setenv("VOICE_WHATSAPP_CONFIRMATION", "1")
    assert "WhatsApp" in twilio_bridge.build_call_context(PAYLOAD, datetime(2026, 11, 7, 12, 0), {})["followUp"]



def test_result_payload_has_the_latest_agreement_and_no_transcript():
    r = twilio_bridge.CallResult(callSid="CA1", companyId=1, clientId=3372, orderId=9)
    r.identityConfirmed = True
    r.transcript = [{"speaker": "customer", "text": "Calle Reforma 123"}]
    r.proposals = [{"capability": "PICKUP_AGREEMENT", "revision": 1, "fields": {"date": "2026-11-09"}},
                   {"capability": "PICKUP_AGREEMENT", "revision": 2, "fields": {"date": "2026-11-10"}},
                   {"capability": "REQUEST_HUMAN", "fields": {"reason": "queja"}}]
    body = twilio_bridge.result_payload(r)
    assert body["agreement"] == {"capability": "PICKUP_AGREEMENT", "revision": 2, "fields": {"date": "2026-11-10"}}
    assert body["requestHuman"] == {"reason": "queja"} and "transcript" not in json.dumps(body)


def test_results_are_not_posted_until_enabled(monkeypatch):
    monkeypatch.delenv("VOICE_DELIVER_RESULTS", raising=False)
    called = []
    monkeypatch.setattr(twilio_bridge.httpx, "AsyncClient", lambda **k: called.append(k))
    asyncio.run(twilio_bridge._post_result(twilio_bridge.CallResult(callSid="CA", companyId=1, clientId=2, orderId=3)))
    assert called == []
