"""
Live phone calls: Twilio Media Streams <-> Gemini Live (voice_secretary_agent).

Flow (the backend side is not built yet):
  smartloans_backend places the call with Twilio REST and TwiML
      <Connect><Stream url="wss://<this app>/voice/twilio-stream">
        <Parameter name="token" value="<signed call token>"/>
      </Stream></Connect>
  Twilio opens this WebSocket: "connected", "start" (with the token in
  customParameters), then "media" frames (μ-law 8 kHz, 20 ms each) until "stop".
  Audio goes to Gemini Live through ADK's run_live; the model's audio comes back
  and is sent to Twilio as "media" frames. Barge-in: when the model reports it
  was interrupted, a "clear" frame drops what Twilio still has buffered.
  Hang-up: the agent calls end_call after its goodbye; once that turn has
  finished playing (Twilio echoes our "mark"), the socket is closed, which ends
  <Connect> and the call.

Nothing here writes: an agreement is only proposed (propose_action) and comes
out in the CallResult. Delivering it to the backend (which confirms it with the
customer by WhatsApp) is the next step; for now it's handed to RESULT_HOOKS
and logged without PII (no transcript, no phone, no address).
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
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.voice_secretary import voice_secretary_agent
from agents.voice_secretary import live_state
from agents.voice_secretary.calendar_check import build_calendar
from agents.voice_secretary.prompt import PROMPT_VERSION
from metering import MeteredRunner, UsageMeter, bind_metering, unbind_metering
from tools.backend_api import get_business_info, get_reservation_hours
from tools.safe_tool import never_raise
from voice.audio import Downsampler24kTo8k, LineLevel, ulaw8k_to_pcm16k, ulaw_energy
from voice.call_token import InvalidCallToken, verify_call_token

logger = logging.getLogger(__name__)

router = APIRouter()

APP_NAME = "loan_agents_voice_secretary"
VOICE_NAME = os.environ.get("VOICE_NAME", "Kore")
WRAP_UP_AFTER_S = 4 * 60   # the agent is asked to close the call
MAX_CALL_S = 6 * 60        # hard cap, whatever happens
END_CALL_GRACE_S = 10      # after end_call: wait this long for the goodbye to play
SILENCE_NUDGE_S = 10       # customer silent this long after the agent finished → nudge
# Local barge-in: Gemini sends audio faster than real time, so when the customer
# talks over the agent Gemini has often finished (no "interrupted") while Twilio
# is still playing its buffer. This many consecutive 20 ms frames above
# the line's speech threshold while playback is pending → "clear". The threshold
# adapts to the line (voice/audio.py LineLevel); BARGE_IN_ENERGY is its ceiling.
BARGE_IN_ENERGY = float(os.environ.get("VOICE_BARGE_IN_ENERGY", "900"))
BARGE_IN_FRAMES = 5
START_TIMEOUT_S = 10       # Twilio sends "start" right after connecting

_WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

_session_service = InMemorySessionService()
_runner = MeteredRunner(agent=voice_secretary_agent, app_name=APP_NAME, session_service=_session_service)

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
    orderId: int | None
    durationS: float = 0.0
    endedBy: str = ""            # agent | customer | timeout | error
    transcript: list[dict] = field(default_factory=list)   # [{"speaker", "text"}]
    proposals: list[dict] = field(default_factory=list)    # propose_action payloads, in order
    agentTurns: int = 0
    bargeIns: int = 0
    repeatedQuestions: int = 0
    lineLevel: dict = field(default_factory=dict)
    identityConfirmed: bool = False
    tokens: dict = field(default_factory=dict)
    promptVersion: str = PROMPT_VERSION

    @property
    def agreement(self) -> dict | None:
        """The agreement to confirm: the LATEST PICKUP_AGREEMENT of the call (a
        customer who changes their mind gets a new one; earlier ones are history)."""
        agreements = [p for p in self.proposals if p.get("capability") == "PICKUP_AGREEMENT"]
        return agreements[-1] if agreements else None

    def safe_summary(self) -> dict:
        """What may be logged: no transcript, no field values (address, names)."""
        return {"callSid": self.callSid, "companyId": self.companyId, "orderId": self.orderId,
                "durationS": round(self.durationS, 1), "endedBy": self.endedBy,
                "agentTurns": self.agentTurns, "bargeIns": self.bargeIns, "repeatedQuestions": self.repeatedQuestions, "lineLevel": self.lineLevel, "identityConfirmed": self.identityConfirmed, "promptVersion": self.promptVersion,
                "proposals": [p.get("capability") for p in self.proposals], "tokens": self.tokens}


def hermosillo_now() -> datetime:
    """Hermosillo is UTC-7 all year — same convention as main.py and tools/backend_api.py."""
    return datetime.now(timezone.utc) - timedelta(hours=7)


_BUSINESS_FIELDS = ("address", "mapsUrl", "phone", "paymentMethods", "servicesInfo",
                    "pickupPolicy", "deliveryPolicy", "extraInfo")


async def fetch_business_facts(company_id: int, branch_id) -> dict:
    """Business info + opening hours, read once when the call connects (~0.6 s,
    in parallel). Mid-call tool round trips added seconds of dead air on the line."""
    info, hours = await asyncio.gather(
        asyncio.to_thread(never_raise(get_business_info), company_id, branch_id),
        asyncio.to_thread(never_raise(get_reservation_hours), company_id))
    business = ({k: info.get(k) for k in _BUSINESS_FIELDS}
                if isinstance(info, dict) and info.get("status") != "unavailable" else "unavailable")
    if isinstance(hours, list):
        by_day = {h.get("dayOfWeek"): h for h in hours if isinstance(h, dict)}
        opening = [{"weekday": name,
                    **({"open": by_day[i].get("openTime"), "close": by_day[i].get("closeTime")}
                       if i in by_day else {"closed": True})}
                   for i, name in enumerate(_WEEKDAYS_ES)]
    else:
        opening = "unavailable"
    return {"business": business, "openingHours": opening}


def order_details(payload: dict) -> dict:
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    return {"service": order.get("service"), "total": order.get("total")}


def available_days(next_days: list[dict], opening, now_local: datetime) -> list[dict] | str:
    """The days a pickup/delivery can actually happen, each with ITS hours.

    A live call (2026-11-07, 22:31) offered "hoy" after closing and read the weekly
    table back wrong ("de 10 a 14 y el sábado de 10 a 17"). The model now gets the
    answer already computed: closed days are left out, and today only appears while
    it's still open, starting from now (rounded up to the next quarter hour)."""
    if not isinstance(opening, list):
        return "unavailable"
    hours = {d.get("weekday"): d for d in opening if isinstance(d, dict)}
    minutes = now_local.hour * 60 + now_local.minute
    rounded = min(minutes + (-minutes % 15), 23 * 60 + 45)
    now_rounded = f"{rounded // 60:02d}:{rounded % 60:02d}"
    out = []
    for i, day in enumerate(next_days):
        h = hours.get(day["weekday"])
        if not h or h.get("closed") or not h.get("open") or not h.get("close"):
            continue
        opens, closes = h["open"], h["close"]
        if i == 0:
            if now_rounded >= closes:
                continue
            opens = max(opens, now_rounded)
        out.append({"date": day["date"], "weekday": day["weekday"],
                    "label": {0: "hoy", 1: "mañana"}.get(i), "open": opens, "close": closes})
    return out


def _spoken_time(hhmm: str) -> str:
    """"14:00" → "las 2 de la tarde" — said the way people say it on the phone."""
    h, m = (int(x) for x in hhmm.split(":"))
    part = "de la mañana" if h < 12 else "del día" if h == 12 else "de la tarde" if h < 19 else "de la noche"
    h12 = h if h <= 12 else h - 12
    article = "la" if h12 == 1 else "las"
    mins = "" if m == 0 else " y media" if m == 30 else f":{m:02d}"
    return f"{article} {h12}{mins} {part}"


def spoken_hours(opening) -> list[str] | str:
    """Weekly hours as sentences, grouping only CONSECUTIVE days with IDENTICAL
    hours. A live call merged everything into "lunes a sábado de 10 a 10" while
    Saturday closes at 17:00 — the grouping is now done here, not by the model."""
    if not isinstance(opening, list):
        return "unavailable"
    by_day = {d.get("weekday"): d for d in opening if isinstance(d, dict)}
    spans = []
    for day in _WEEKDAYS_ES:
        d = by_day.get(day)
        key = None if not d or d.get("closed") else (d.get("open"), d.get("close"))
        if spans and spans[-1][2] == key:
            spans[-1][1] = day
        else:
            spans.append([day, day, key])
    out = []
    for first, last, key in spans:
        days = first.capitalize() if first == last else f"De {first} a {last}"
        out.append(f"{days}: cerrado" if key is None
                   else f"{days}: de {_spoken_time(key[0])} a {_spoken_time(key[1])}")
    return out


# What the agent promises after the call. WhatsApp only once the backend really
# sends the summary and executes it after the customer's "sí" (not built yet) —
# until then, promising a WhatsApp that never comes is a lie to the customer.
FOLLOW_UP_WHATSAPP = "le va a llegar un WhatsApp con el resumen para que lo confirme"
FOLLOW_UP_TEAM = "alguien del equipo se va a comunicar con usted para confirmarlo"


def follow_up_line() -> str:
    return FOLLOW_UP_WHATSAPP if os.environ.get("VOICE_WHATSAPP_CONFIRMATION") == "1" else FOLLOW_UP_TEAM


def _registered(value) -> bool:
    return bool(value) and not str(value).startswith("NO REGISTRADO")


# What the model sees of the context: the raw weekly table and the full list of
# dates stay out (they're kept for the deterministic calendar check only).
_HIDDEN_FROM_MODEL = ("openingHours", "nextDays")


def build_call_context(payload: dict, now_local: datetime, facts: dict | None = None) -> dict:
    """What the agent is told at the start: display values from the signed token,
    plus the business facts read from the backend. Deliberately no order details."""
    next_days = [{"date": d.isoformat(), "weekday": _WEEKDAYS_ES[d.weekday()]}
                 for d in (now_local.date() + timedelta(days=i) for i in range(8))]
    facts = facts or {}
    return {
        **facts,
        "companyName": payload.get("companyName"),
        "branchName": payload.get("branchName"),
        "customerName": payload.get("customerName"),
        "today": now_local.date().isoformat(),
        "nowTime": now_local.strftime("%H:%M"),
        "nextDays": next_days,
        "availableDays": available_days(next_days, facts.get("openingHours"), now_local),
        "hoursSpoken": spoken_hours(facts.get("openingHours")),
        # Decided here, not by the model: delivery was offered on a real call with
        # no delivery policy registered.
        "deliveryAllowed": isinstance(facts.get("business"), dict)
                           and _registered(facts["business"].get("deliveryPolicy")),
        "paymentMethodsRegistered": isinstance(facts.get("business"), dict)
                                    and _registered(facts["business"].get("paymentMethods")),
        "followUp": follow_up_line(),
    }


def model_context(context: dict) -> dict:
    return {k: v for k, v in context.items() if k not in _HIDDEN_FROM_MODEL}


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
                                 clientId=payload["clientId"], orderId=payload.get("orderId"))
        self.queue = LiveRequestQueue()
        self.downsampler = Downsampler24kTo8k()
        self.ending = False                 # end_call was called
        self.muted = False                  # goodbye done: no more agent audio goes out
        self._spoke_since_customer = False  # agent audio went out since the customer last spoke
        self._last_activity = time.monotonic()
        self._agent_talking = False
        self._playback_until = 0.0          # when Twilio will have played what we sent
        self._loud_frames = 0
        self.level = LineLevel(ceiling=BARGE_IN_ENERGY)
        self.hangup = asyncio.Event()       # close the socket now
        self._mark_seq = 0
        self._pending_mark: str | None = None
        self._agent_text: list[str] = []
        self._customer_text: list[str] = []
        self._meter = UsageMeter(payload["companyId"], "/voice/twilio-stream", "voice_secretary_agent", "")

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
                logger.exception("[voice] transcript hook failed")

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
                            self._agent_talking = True
                            now = time.monotonic()
                            self._playback_until = max(now, self._playback_until) + len(ulaw) / 8000
                            await self._send({"event": "media",
                                              "media": {"payload": base64.b64encode(ulaw).decode("ascii")}})

                delta = event.actions.state_delta if event.actions else {}
                if delta.get("pending_action"):
                    proposal = dict(delta["pending_action"])
                    if proposal.get("capability") == "PICKUP_AGREEMENT":
                        # The backend must only ever confirm the latest revision of a call.
                        proposal["revision"] = 1 + sum(p.get("capability") == "PICKUP_AGREEMENT"
                                                       for p in self.result.proposals)
                    self.result.proposals.append(proposal)
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

                if event.turn_complete:
                    self._flush_turn("customer")
                    self._flush_turn("agent")
                    self.result.agentTurns += 1
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
            await asyncio.sleep(1)
            # Quiet counts from the later of: the customer's last sound, or the moment
            # the agent's audio finishes playing at Twilio (not when Gemini sent it).
            quiet = time.monotonic() - max(self._last_activity, self._playback_until)
            if self._agent_talking or self.ending or quiet < SILENCE_NUDGE_S:
                continue
            nudges += 1
            self._last_activity = time.monotonic()
            self.queue.send_content(_system_text(
                "[Sistema] El cliente no ha dicho nada en varios segundos. Pregunta brevemente si sigue en la línea."
                if nudges == 1 else
                f"[Sistema] El cliente sigue sin responder. Dile que {follow_up_line()}, "
                "despídete y termina la llamada."))

    async def _timers(self) -> None:
        await asyncio.sleep(WRAP_UP_AFTER_S)
        self.queue.send_content(_system_text(
            "[Sistema] La llamada ya lleva varios minutos: resume el acuerdo, despídete y termina la llamada."))
        await asyncio.sleep(MAX_CALL_S - WRAP_UP_AFTER_S)
        self.result.endedBy = self.result.endedBy or "timeout"
        self.hangup.set()

    async def run(self) -> CallResult:
        started = time.monotonic()
        self._user_id = f"voice-{self.result.companyId}-{self.result.clientId}"
        self._session_id = f"voice-{self.result.callSid or uuid.uuid4().hex}"
        facts = await fetch_business_facts(self.result.companyId, self.payload.get("branchId"))
        context = build_call_context(self.payload, hermosillo_now(), facts)
        live_state.register(self._session_id)
        await _session_service.create_session(
            app_name=APP_NAME, user_id=self._user_id, session_id=self._session_id,
            state={"voice_calendar": build_calendar(context),   # propose_action checks dates against it
                   "voice_delivery_allowed": context["deliveryAllowed"],
                   "voice_company_id": self.result.companyId,
                   "voice_branch_id": self.payload.get("branchId"),
                   "voice_client_id": self.result.clientId,
                   "voice_order_id": self.result.orderId,
                   # Handed to the model only by confirm_customer_identity — never in
                   # the opening context, so a wrong person or a voicemail can't hear it.
                   "voice_order": order_details(self.payload),
                   "voice_customer_name": self.payload.get("customerName")})
        self.queue.send_content(_system_text(
            "[Sistema] El cliente acaba de contestar la llamada. Haz tu saludo de apertura ahora.\n"
            f"Context: {json.dumps(model_context(context), ensure_ascii=False)}"))

        tasks = [asyncio.create_task(self._upstream()), asyncio.create_task(self._downstream()),
                 asyncio.create_task(self._timers()), asyncio.create_task(self._silence_watchdog()),
                 asyncio.create_task(self.hangup.wait())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception() is not None:
                    self.result.endedBy = "error"
                    logger.error("[voice] call %s failed: %r", self.result.callSid, task.exception())
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


def result_payload(result: CallResult) -> dict:
    """Body for smartloans_backend POST /voiceCalls/result (posgmo-factory
    tests/prd_voiceCall.json). Server-to-server only; no transcript."""
    agreement = result.agreement
    human = next((p for p in reversed(result.proposals) if p.get("capability") == "REQUEST_HUMAN"), None)
    return {
        "callSid": result.callSid, "companyId": result.companyId, "clientId": result.clientId,
        "orderId": result.orderId, "endedBy": result.endedBy, "durationS": round(result.durationS, 1),
        "promptVersion": result.promptVersion, "identityConfirmed": result.identityConfirmed,
        "agreement": ({"capability": "PICKUP_AGREEMENT", "revision": agreement.get("revision"),
                       "fields": agreement.get("fields") or {}} if agreement else None),
        "requestHuman": {"reason": (human.get("fields") or {}).get("reason")} if human else None,
    }


async def _post_result(result: CallResult) -> None:
    """The backend sends the WhatsApp summary and waits for the customer's "sí".
    Off until that endpoint exists (VOICE_DELIVER_RESULTS=1). Never raises."""
    if os.environ.get("VOICE_DELIVER_RESULTS") != "1":
        return
    from config.settings import SMARTLOANS_BACKEND_URL, WORKER_KEY
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{SMARTLOANS_BACKEND_URL}/voiceCalls/result", json=result_payload(result),
                                     headers={"X-Worker-Key": WORKER_KEY} if WORKER_KEY else {})
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — the call is over; staff can still see it in the logs
        logger.error("[voice] could not deliver result of %s: %s", result.callSid, type(exc).__name__)


def _deliver(result: CallResult) -> None:
    logger.info("[voice] call finished %s", json.dumps(result.safe_summary()))
    if os.environ.get("VOICE_DEBUG_TRANSCRIPT") == "1":
        # Local test calls only: the transcript is customer data — never enable in production.
        print(json.dumps({"transcript": result.transcript, "agreement": result.agreement,
                          "proposals": result.proposals, **result.safe_summary()},
                         ensure_ascii=False, indent=1), flush=True)
    for hook in RESULT_HOOKS:
        try:
            hook(result)
        except Exception:  # noqa: BLE001
            logger.exception("[voice] result hook failed")


_warned_unsigned = False


def stream_url(ws: WebSocket) -> str:
    """The URL Twilio signed: the <Stream url> exactly as configured. Behind a proxy
    (ngrok, Azure) the Host header is the public host; VOICE_PUBLIC_STREAM_URL
    overrides it when that isn't true."""
    override = os.environ.get("VOICE_PUBLIC_STREAM_URL")
    if override:
        return override
    query = ws.url.query
    return f"wss://{ws.headers.get('host', '')}{ws.url.path}" + (f"?{query}" if query else "")


def twilio_signature_ok(url: str, signature: str | None, auth_token: str) -> bool:
    """X-Twilio-Signature on a Media Streams WebSocket: base64(HMAC-SHA1(auth token, URL)) —
    no body parameters on a WebSocket upgrade."""
    expected = base64.b64encode(hmac.new(auth_token.encode(), url.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature or "")


@router.websocket("/voice/twilio-stream")
async def twilio_stream(ws: WebSocket) -> None:
    # Two locks: the Twilio signature proves Twilio opened this socket; the signed
    # call token (below) proves OUR backend placed the call and fixes its company/client.
    global _warned_unsigned
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if auth_token:
        if not twilio_signature_ok(stream_url(ws), ws.headers.get("x-twilio-signature"), auth_token):
            logger.warning("[voice] stream refused: bad Twilio signature")
            await ws.close(code=1008)
            return
    elif not _warned_unsigned:
        logger.warning("[voice] TWILIO_AUTH_TOKEN is not set — Twilio signatures are NOT checked "
                       "(the signed call token still is). Set it in production.")
        _warned_unsigned = True
    await ws.accept()
    try:
        start = await asyncio.wait_for(_wait_for_start(ws), START_TIMEOUT_S)
        params = start.get("customParameters") or {}
        payload = verify_call_token(params.get("token", ""), os.environ.get("VOICE_CALL_SECRET", ""))
    except (asyncio.TimeoutError, InvalidCallToken, WebSocketDisconnect, ValueError, KeyError) as exc:
        logger.warning("[voice] stream refused: %s", type(exc).__name__ if not isinstance(exc, InvalidCallToken) else exc)
        try:
            await ws.close(code=1008)
        except RuntimeError:
            pass
        return

    tokens = bind_metering(payload["companyId"], "/voice/twilio-stream")
    try:
        call = TwilioCall(ws, payload, stream_sid=start.get("streamSid", ""), call_sid=start.get("callSid", ""))
        result = await call.run()
    finally:
        unbind_metering(tokens)
    _deliver(result)
    await _post_result(result)
    try:
        await ws.close()
    except RuntimeError:
        pass
