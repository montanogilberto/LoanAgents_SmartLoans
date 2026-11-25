"""
Live calls to a proposed aval — copy of voice/collections_bridge.py for
aval_voice_agent. Independent: its own route and its own secret
(AVAL_CALL_SECRET; unset = every call is refused, so it is OFF by default).

This module answers a stream; it can't dial. smartloans_backend dials and signs who
is called and for whom. Nothing is written and no result is delivered anywhere yet:
the CallResult goes to RESULT_HOOKS. No transcript, name or phone is logged.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.aval_voice import aval_voice_agent
from agents.aval_voice import live_state, rules
from agents.aval_voice.prompt import PROMPT_VERSION
from metering import MeteredRunner, UsageMeter, bind_metering, unbind_metering
from loan_collections.handoff import request_human
from loan_collections.reminders import may_contact
from loan_collections.situation import hermosillo_now
from voice.audio import Downsampler24kTo8k, LineLevel, ulaw8k_to_pcm16k, ulaw_energy
from voice.call_token import InvalidCallToken, verify_call_token

logger = logging.getLogger(__name__)

router = APIRouter()

APP_NAME = "loan_agents_aval_voice"
VOICE_NAME = os.environ.get("VOICE_NAME", "Kore")
WRAP_UP_AFTER_S = 4 * 60   # the agent is asked to close the call
MAX_CALL_S = 6 * 60        # hard cap, whatever happens
END_CALL_GRACE_S = 10      # after end_call: wait this long for the goodbye to play
UNANSWERED_S = 3.5         # the customer spoke and got no answer → wake the model
SILENCE_NUDGE_S = 10       # customer silent this long after the agent finished → nudge
# Local barge-in: Gemini sends audio faster than real time, so when the customer
# talks over the agent Gemini has often finished (no "interrupted") while Twilio
# is still playing its buffer. This many consecutive 20 ms frames above
# the line's speech threshold while playback is pending → "clear". The threshold
# adapts to the line (voice/audio.py LineLevel); BARGE_IN_ENERGY is its ceiling.
BARGE_IN_ENERGY = float(os.environ.get("VOICE_BARGE_IN_ENERGY", "900"))
BARGE_IN_FRAMES = 5
START_TIMEOUT_S = 10       # Twilio sends "start" right after connecting

_session_service = InMemorySessionService()
_runner = MeteredRunner(agent=aval_voice_agent, app_name=APP_NAME, session_service=_session_service)

# Called with each finished CallResult (the dev simulator uses this; the
# backend delivery will too). A hook that raises is logged and skipped.
RESULT_HOOKS: list[Callable[["CallResult"], None]] = []
# Called with ("customer" | "agent", text) as each turn's transcript completes.
TRANSCRIPT_HOOKS: list[Callable[[str, str], None]] = []


@dataclass
class CallResult:
    callSid: str
    companyId: int
    clientId: int
    guarantorId: int
    durationS: float = 0.0
    endedBy: str = ""            # agent | customer | timeout | error
    transcript: list[dict] = field(default_factory=list)   # [{"speaker", "text"}]
    proposals: list[dict] = field(default_factory=list)    # propose_action payloads, in order
    agentTurns: int = 0
    bargeIns: int = 0
    repeatedQuestions: int = 0
    toolTrace: list[dict] = field(default_factory=list)   # tool name + ok/error, no arguments
    lineLevel: dict = field(default_factory=dict)
    identityConfirmed: bool = False
    tokens: dict = field(default_factory=dict)
    promptVersion: str = PROMPT_VERSION

    @property
    def outcome(self) -> dict | None:
        """The LATEST GUARANTOR_CALL_RESULT, or None when a person must review it."""
        if any(p.get("capability") == "REQUEST_HUMAN" for p in self.proposals):
            return None
        found = [p for p in self.proposals if p.get("capability") == "GUARANTOR_CALL_RESULT"]
        return found[-1] if found else None

    def safe_summary(self) -> dict:
        """What may be logged: no transcript, no field values (address, names)."""
        return {"callSid": self.callSid, "companyId": self.companyId, "guarantorId": self.guarantorId,
                "durationS": round(self.durationS, 1), "endedBy": self.endedBy,
                "agentTurns": self.agentTurns, "bargeIns": self.bargeIns, "repeatedQuestions": self.repeatedQuestions, "lineLevel": self.lineLevel, "identityConfirmed": self.identityConfirmed, "promptVersion": self.promptVersion,
                "proposals": [p.get("capability") for p in self.proposals], "tokens": self.tokens}


FOLLOW_UP_TEAM = "alguien del equipo le dará seguimiento"


def follow_up_line() -> str:
    return FOLLOW_UP_TEAM


def build_call_context(payload: dict, now_local: datetime) -> dict:
    """What the model is told first: no surname, no applicant, no word about a loan."""
    first = ((payload.get("guarantorName") or "").split() or [""])[0]
    return {"companyName": payload.get("companyName"), "firstName": first,
            "today": now_local.date().isoformat(), "followUp": follow_up_line(),
            "opening": f"Hola, le llamo de parte de {payload.get('companyName')}. Soy su asistente virtual y "
                       f"esta llamada puede ser grabada. ¿Hablo con {first}?"}


def refusal(payload: dict, now_local: datetime) -> str | None:
    """Why this call must not happen, or None. Everything is signed by the backend;
    missing means unknown, so the call is refused."""
    guarantor = payload.get("guarantorId")
    if payload.get("purpose") != "aval" or not isinstance(guarantor, int) or isinstance(guarantor, bool):
        return "not_an_aval_token"
    for key in ("guarantorName", "applicantName"):
        if not isinstance(payload.get(key), str) or len(payload[key].split()) < 2:
            return f"{key}_incomplete"
    if not payload.get("companyName"):
        return "no_company_name"
    if payload.get("applicantConfirmedNotice") is not True:
        return "aval_not_notified"          # the app's checkbox: the aval agreed to share their phone
    contacts = payload.get("contactsToday")
    if not isinstance(contacts, int) or isinstance(contacts, bool) or not isinstance(payload.get("optedOut"), bool):
        return "contact_history_unknown"
    return may_contact(now_local, contacts, payload["optedOut"])


def live_run_config() -> RunConfig:
    return RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE_NAME))),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Phone audio is quiet and narrowband: a customer's answer on a landline went
        # unheard (2026-11-07). HIGH = start a turn on softer speech.
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH)),
    )


def _system_text(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


class TwilioCall:
    """One phone call: pumps audio both ways until hang-up, then returns a CallResult."""

    def __init__(self, ws: WebSocket, payload: dict, stream_sid: str, call_sid: str, runner=None):
        self.ws = ws
        self.payload = payload
        self.stream_sid = stream_sid
        self.runner = runner or _runner
        self.result = CallResult(callSid=call_sid, companyId=payload["companyId"],
                                 clientId=payload["clientId"], guarantorId=payload["guarantorId"])
        self.queue = LiveRequestQueue()
        self.downsampler = Downsampler24kTo8k()
        self.ending = False                 # end_call was called
        self.muted = False                  # goodbye done: no more agent audio goes out
        self._spoke_since_customer = False  # agent audio went out since the customer last spoke
        self._last_activity = time.monotonic()
        self._agent_talking = False
        self._playback_until = 0.0          # when Twilio will have played what we sent
        self._loud_frames = 0
        self._heard_at: float | None = None   # customer speech the agent has not answered yet
        self._tools_pending = 0
        self._stop_noted = False
        self.level = LineLevel(ceiling=BARGE_IN_ENERGY)
        self.hangup = asyncio.Event()       # close the socket now
        self._mark_seq = 0
        self._pending_mark: str | None = None
        self._agent_text: list[str] = []
        self._customer_text: list[str] = []
        self._meter = UsageMeter(payload["companyId"], "/voice/aval-stream", "aval_voice_agent", "")

    # ── Twilio → Gemini ────────────────────────────────────────────────
    async def _upstream(self) -> None:
        try:
            while not self.hangup.is_set():
                frame = json.loads(await self.ws.receive_text())
                kind = frame.get("event")
                if kind == "media":
                    ulaw = base64.b64decode(frame["media"]["payload"])
                    await self._check_barge_in(ulaw)
                    self.queue.send_realtime(types.Blob(data=ulaw8k_to_pcm16k(ulaw),
                                                        mime_type="audio/pcm;rate=16000"))
                elif kind == "mark":
                    if frame.get("mark", {}).get("name") == self._pending_mark:
                        self.hangup.set()
                elif kind == "stop":
                    self.result.endedBy = self.result.endedBy or "customer"
                    return
        except WebSocketDisconnect:
            self.result.endedBy = self.result.endedBy or "customer"

    async def _check_barge_in(self, ulaw: bytes) -> None:
        if not self.level.is_speech(ulaw_energy(ulaw)):
            self._loud_frames = 0
            return
        self._loud_frames += 1
        self._last_activity = time.monotonic()   # the customer is talking (transcripts arrive late)
        if self._loud_frames >= BARGE_IN_FRAMES:
            self._heard_at = time.monotonic()
        if (self._loud_frames == BARGE_IN_FRAMES and not self.muted
                and time.monotonic() < self._playback_until - 0.1):
            self._playback_until = 0.0
            self.result.bargeIns += 1
            await self._send({"event": "clear"})

    # ── Gemini → Twilio ────────────────────────────────────────────────
    async def _send(self, frame: dict) -> None:
        await self.ws.send_text(json.dumps({**frame, "streamSid": self.stream_sid}))

    def _flush_turn(self, speaker: str) -> None:
        parts = self._agent_text if speaker == "agent" else self._customer_text
        text = "".join(parts).strip()
        parts.clear()
        if not text:
            return
        if speaker == "agent" and text.endswith("?"):
            previous = next((t["text"] for t in reversed(self.result.transcript) if t["speaker"] == "agent"), "")
            if difflib.SequenceMatcher(None, previous.lower(), text.lower()).ratio() >= 0.8:
                self.result.repeatedQuestions += 1
        self.result.transcript.append({"speaker": speaker, "text": text})
        for hook in TRANSCRIPT_HOOKS:
            try:
                hook(speaker, text)
            except Exception:  # noqa: BLE001 — a debug hook never breaks the call
                logger.exception("[aval] transcript hook failed")

    def _note_stop(self) -> None:
        """"Ya no me llamen": close right away; the code records it (ensure_outcome)."""
        if self._stop_noted or self.ending:
            return
        if rules.asks_to_stop(live_state.customer_text_since(self._session_id, 0.0)):
            self._stop_noted = True
            self.queue.send_content(_system_text(
                "[Sistema] La persona pidió que no la llamen. Llama request_human, discúlpate, "
                "despídete y termina la llamada. No preguntes nada más."))

    async def _hang_up_after_goodbye(self) -> None:
        """The goodbye is queued in Twilio; a mark comes back once it has played."""
        self._mark_seq += 1
        self._pending_mark = f"hangup-{self._mark_seq}"
        await self._send({"event": "mark", "mark": {"name": self._pending_mark}})
        await asyncio.sleep(END_CALL_GRACE_S)
        self.hangup.set()

    async def _downstream(self) -> None:
        hangup_task = None
        try:
            async for event in self.runner.run_live(
                    user_id=self._user_id, session_id=self._session_id,
                    live_request_queue=self.queue, run_config=live_run_config()):
                self._meter.add(event)   # a copy for the CallResult; MeteredRunner bills it

                if event.interrupted:
                    self.downsampler.reset()
                    self._playback_until = 0.0
                    await self._send({"event": "clear"})
                    self._flush_turn("agent")
                    self._spoke_since_customer = False
                    self._agent_talking = False
                    self._last_activity = time.monotonic()

                # An agent turn ends only on turn_complete or interrupted: the
                # customer's transcription often arrives late, while the agent is
                # already answering, and must not split the agent's turn.
                if event.input_transcription and event.input_transcription.text and event.partial:
                    self._customer_text.append(event.input_transcription.text)
                    live_state.add_customer_text(self._session_id, event.input_transcription.text)
                    self._spoke_since_customer = False
                    self._last_activity = time.monotonic()
                if (event.output_transcription and event.output_transcription.text and event.partial
                        and not self.muted):
                    if self._customer_text:
                        self._flush_turn("customer")
                    self._agent_text.append(event.output_transcription.text)

                for part in (event.content.parts if event.content and event.content.parts else []):
                    blob = part.inline_data
                    if blob and blob.data and (blob.mime_type or "").startswith("audio/") and not self.muted:
                        ulaw = self.downsampler.convert(blob.data)
                        if ulaw:
                            self._spoke_since_customer = True
                            if not self._loud_frames:
                                self._heard_at = None
                            self._agent_talking = True
                            now = time.monotonic()
                            self._playback_until = max(now, self._playback_until) + len(ulaw) / 8000
                            await self._send({"event": "media",
                                              "media": {"payload": base64.b64encode(ulaw).decode("ascii")}})

                delta = event.actions.state_delta if event.actions else {}
                if delta.get("pending_action"):
                    self.result.proposals.append(dict(delta["pending_action"]))
                if delta.get("voice_identity_confirmed"):
                    self.result.identityConfirmed = True
                if delta.get("voice_end_call"):
                    self.ending = True
                    self.result.endedBy = "agent"
                    if self._spoke_since_customer and hangup_task is None:
                        # The goodbye is already out. After end_call returns the model
                        # often says it again — drop that and hang up once it has played.
                        self.muted = True
                        hangup_task = asyncio.create_task(self._hang_up_after_goodbye())

                for call in (event.get_function_calls() or []) if hasattr(event, "get_function_calls") else []:
                    self._tools_pending += 1
                    self.result.toolTrace.append({"tool": call.name})
                for resp in (event.get_function_responses() or []) if hasattr(event, "get_function_responses") else []:
                    self._tools_pending = max(0, self._tools_pending - 1)
                    self._heard_at = self._heard_at and time.monotonic()   # the answer starts now
                    out = resp.response or {}
                    # Outcome only (ok / error text) — never the arguments, which hold PII.
                    self.result.toolTrace.append({"tool": resp.name, "result": "error" if out.get("errors") or out.get("error")
                                                  else "ok", **({"errors": out.get("errors") or [out.get("error")]}
                                                                 if out.get("errors") or out.get("error") else {})})

                if event.turn_complete:
                    self._flush_turn("customer")
                    self._flush_turn("agent")
                    self.result.agentTurns += 1
                    self._note_stop()
                    live_state.mark_agent_turn_end(self._session_id)
                    self._agent_talking = False
                    self._last_activity = time.monotonic()
                    if self.ending and hangup_task is None:   # end_call came before the goodbye
                        self.muted = True
                        hangup_task = asyncio.create_task(self._hang_up_after_goodbye())
        finally:
            if hangup_task:
                hangup_task.cancel()

    async def _silence_watchdog(self) -> None:
        """Gemini Live only speaks when it hears something: if the customer goes
        quiet, the line would stay silent until the wrap-up timer."""
        nudges = 0
        while True:
            await asyncio.sleep(0.25)
            if (self._heard_at is not None and not self._tools_pending and not self.ending
                    and time.monotonic() - self._heard_at >= UNANSWERED_S):
                # Gemini sometimes lets a short "Sí" pass: 10 s of dead air and lost answers.
                self._heard_at = None
                said = "".join(self._customer_text).strip()
                self.queue.send_content(_system_text(
                    "[Sistema] La persona acaba de responder a lo último que preguntaste"
                    + (f' ("{said}")' if said else ". Si no lo escuchaste, pídele que lo repita")
                    + ". Continúa ahora; si falta llamar una herramienta, llámala."))
            # Quiet counts from the later of: the customer's last sound, or the moment
            # the agent's audio finishes playing at Twilio (not when Gemini sent it).
            quiet = time.monotonic() - max(self._last_activity, self._playback_until)
            if self._agent_talking or self.ending or quiet < SILENCE_NUDGE_S:
                continue
            nudges += 1
            self._last_activity = time.monotonic()
            self.queue.send_content(_system_text(
                "[Sistema] La persona no ha dicho nada en varios segundos. Pregunta brevemente si sigue en la línea."
                if nudges == 1 else
                f"[Sistema] La persona sigue sin responder. Dile que {follow_up_line()}, "
                "despídete y termina la llamada."))

    async def _timers(self) -> None:
        await asyncio.sleep(WRAP_UP_AFTER_S)
        self.queue.send_content(_system_text(
            "[Sistema] La llamada ya lleva varios minutos: resume lo acordado, despídete y termina la llamada."))
        await asyncio.sleep(MAX_CALL_S - WRAP_UP_AFTER_S)
        self.result.endedBy = self.result.endedBy or "timeout"
        self.hangup.set()

    async def run(self) -> CallResult:
        started = time.monotonic()
        self._user_id = f"aval-{self.result.companyId}-{self.result.clientId}"
        self._session_id = f"aval-{self.result.callSid or uuid.uuid4().hex}"
        context = build_call_context(self.payload, hermosillo_now())
        live_state.register(self._session_id)
        await _session_service.create_session(
            app_name=APP_NAME, user_id=self._user_id, session_id=self._session_id,
            # Handed to the model only by confirm_guarantor_identity; the declared
            # relationship never is.
            state={"aval_guarantor_name": self.payload["guarantorName"],
                   "aval_applicant_name": self.payload["applicantName"],
                   "aval_declared_relationship": self.payload.get("relationship")})
        self.queue.send_content(_system_text(
            "[Sistema] La persona acaba de contestar la llamada. Haz tu saludo de apertura ahora.\n"
            f"Context: {json.dumps(context, ensure_ascii=False)}"))

        tasks = [asyncio.create_task(self._upstream()), asyncio.create_task(self._downstream()),
                 asyncio.create_task(self._timers()), asyncio.create_task(self._silence_watchdog()),
                 asyncio.create_task(self.hangup.wait())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception() is not None:
                    self.result.endedBy = "error"
                    logger.error("[aval] call %s failed: %r", self.result.callSid, task.exception())
        finally:
            self.queue.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._flush_turn("customer")
            self._flush_turn("agent")
            live_state.unregister(self._session_id)
            await _session_service.delete_session(
                app_name=APP_NAME, user_id=self._user_id, session_id=self._session_id)
        self.result.durationS = time.monotonic() - started
        self.result.lineLevel = self.level.stats()
        self.result.tokens = {"input": self._meter.input_tokens, "output": self._meter.output_tokens,
                              "thoughts": self._meter.thoughts_tokens}
        return self.result


async def _wait_for_start(ws: WebSocket) -> dict:
    while True:
        frame = json.loads(await ws.receive_text())
        if frame.get("event") == "start":
            return frame["start"]


def ensure_outcome(result: CallResult) -> None:
    """Every call that reached someone leaves an outcome, and doubt goes to a person."""
    said = " ".join(t["text"] for t in result.transcript if t["speaker"] == "customer")
    if rules.asks_to_stop(said):
        result.proposals[:] = [request_human("pide no ser contactado")]
    elif not result.identityConfirmed:
        if rules.says_wrong_number(said):
            result.proposals[:] = [request_human("número equivocado")]
        elif not result.proposals and any(t["tool"] == "confirm_guarantor_identity" for t in result.toolTrace):
            result.proposals.append(request_human("no se pudo verificar la identidad"))
    elif not result.proposals:
        result.proposals.append(request_human("llamada sin resultado registrado: revisar"))


def _deliver(result: CallResult) -> None:
    ensure_outcome(result)
    logger.info("[aval] call finished %s", json.dumps(result.safe_summary()))
    for hook in RESULT_HOOKS:
        try:
            hook(result)
        except Exception:  # noqa: BLE001
            logger.exception("[aval] result hook failed")


_warned_unsigned = False


def stream_url(ws: WebSocket) -> str:
    """The URL Twilio signed: the <Stream url> exactly as configured. Behind a proxy
    (ngrok, Azure) the Host header is the public host; AVAL_PUBLIC_STREAM_URL
    overrides it when that isn't true."""
    override = os.environ.get("AVAL_PUBLIC_STREAM_URL")
    if override:
        return override
    query = ws.url.query
    return f"wss://{ws.headers.get('host', '')}{ws.url.path}" + (f"?{query}" if query else "")


def twilio_signature_ok(url: str, signature: str | None, auth_token: str) -> bool:
    """X-Twilio-Signature on a Media Streams WebSocket: base64(HMAC-SHA1(auth token, URL)) —
    no body parameters on a WebSocket upgrade."""
    expected = base64.b64encode(hmac.new(auth_token.encode(), url.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature or "")


@router.websocket("/voice/aval-stream")
async def twilio_stream(ws: WebSocket) -> None:
    # Two locks: the Twilio signature proves Twilio opened this socket; the signed
    # call token (below) proves OUR backend placed the call and fixes its company/client.
    global _warned_unsigned
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if auth_token:
        if not twilio_signature_ok(stream_url(ws), ws.headers.get("x-twilio-signature"), auth_token):
            logger.warning("[aval] stream refused: bad Twilio signature")
            await ws.close(code=1008)
            return
    elif not _warned_unsigned:
        logger.warning("[aval] TWILIO_AUTH_TOKEN is not set — Twilio signatures are NOT checked "
                       "(the signed call token still is). Set it in production.")
        _warned_unsigned = True
    await ws.accept()
    try:
        start = await asyncio.wait_for(_wait_for_start(ws), START_TIMEOUT_S)
        params = start.get("customParameters") or {}
        payload = verify_call_token(params.get("token", ""), os.environ.get("AVAL_CALL_SECRET", ""))
    except (asyncio.TimeoutError, InvalidCallToken, WebSocketDisconnect, ValueError, KeyError) as exc:
        logger.warning("[aval] stream refused: %s", type(exc).__name__ if not isinstance(exc, InvalidCallToken) else exc)
        try:
            await ws.close(code=1008)
        except RuntimeError:
            pass
        return

    why = refusal(payload, hermosillo_now())
    if why:
        logger.warning("[aval] call refused: %s", why)
        await ws.close(code=1008)
        return

    tokens = bind_metering(payload["companyId"], "/voice/aval-stream")
    try:
        call = TwilioCall(ws, payload, stream_sid=start.get("streamSid", ""), call_sid=start.get("callSid", ""))
        result = await call.run()
    finally:
        unbind_metering(tokens)
    _deliver(result)
    try:
        await ws.close()
    except RuntimeError:
        pass
