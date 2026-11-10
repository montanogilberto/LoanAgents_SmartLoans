"""
Live test matrix for the voice secretary — WITHOUT a phone. Plays Twilio
against the real /voice/twilio-stream route, with real Gemini Live on the
other end, and checks each call against what it should have produced.

  * Runs the route in-process with uvicorn and connects over a real WebSocket,
    sending Twilio-shaped frames (μ-law 8 kHz, 20 ms, paced in real time;
    silence between turns, like a phone line).
  * The "customer" is gemini-2.5-flash playing a persona; its lines are spoken
    with macOS `say` (Paulina, es-MX), so the agent hears real audio.
  * Business info comes from fixtures (FULL: delivers, payment methods
    registered; BARE: neither) so results are repeatable. Opening hours are
    the company's real ones (--company-id), read once from the backend
    (sp_reservationHours) through the same fetch the live bridge uses.
  * Waits for the agent's audio to finish "playing" before answering (except
    the barge-in case), echoes marks like Twilio, and measures latency = end of
    customer speech → first agent audio.
  * Each case has checks (expected tool calls, agreement fields, how the call
    ends, what must not be said); the summary is a hit rate per case.

Nothing is written anywhere: the agent's only backend tool (get_faqs) reads.

  python scripts/voice_sim_call.py --scenario all --runs 3
  python scripts/voice_sim_call.py --scenario pickup
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ.setdefault("VOICE_CALL_SECRET", secrets.token_hex(16))
# The simulator is not Twilio and can't sign like it: run the route without the
# signature check (the signed call token is still verified).
os.environ.pop("TWILIO_AUTH_TOKEN", None)

import uvicorn  # noqa: E402
import websockets  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from google import genai  # noqa: E402

from voice import twilio_bridge  # noqa: E402
from voice.audio import pcm16_to_ulaw  # noqa: E402
from voice.call_token import sign_call_token  # noqa: E402

FRAME = 160            # 20 ms of μ-law at 8 kHz
SILENCE = b"\xff" * FRAME
NOT_REG = "NO REGISTRADO — el negocio no lo ha cargado; no lo inventes ni lo menciones como dato"

# The live bridge's fetch, kept before one_call swaps in the fixture.
_FETCH_BUSINESS_FACTS = twilio_bridge.fetch_business_facts
# Filled by load_opening_hours() from the backend: [{"weekday", "open", "close"} | {"weekday", "closed"}].
OPENING: list[dict] = []
FULL = {"business": {"address": "Calle Uno 100, Centro", "mapsUrl": NOT_REG, "phone": NOT_REG,
                     "paymentMethods": "Efectivo y tarjeta de débito o crédito",
                     "servicesInfo": NOT_REG,
                     "pickupPolicy": "Puede recoger en sucursal en horario de apertura.",
                     "deliveryPolicy": "Entregamos a domicilio sin costo dentro de la ciudad, de lunes a "
                                       "viernes de 12:00 a 18:00.",
                     "extraInfo": NOT_REG},
        "openingHours": OPENING}
BARE = {"business": {k: NOT_REG for k in FULL["business"]}, "openingHours": OPENING}


async def load_opening_hours(company_id: int) -> None:
    """The company's registered week, from the backend — never hard-coded here."""
    facts = await _FETCH_BUSINESS_FACTS(company_id, None)
    opening = facts.get("openingHours")
    if not isinstance(opening, list):
        sys.exit(f"Could not read the opening hours of companyId={company_id} from the backend "
                 f"(/reservationHours); check SMARTLOANS_BACKEND_URL in .env.")
    if all(d.get("closed") for d in opening):
        sys.exit(f"companyId={company_id} has no opening hours registered (reservationHours is empty).")
    OPENING[:] = opening
    print("Horario (DB):", ", ".join(f"{d['weekday']} {'cerrado' if d.get('closed') else d['open'] + '–' + d['close']}"
                                    for d in opening))

# Synthetic client: 1 is the walk-in placeholder, which the bridge refuses to call.
PAYLOAD = {"companyId": 1, "branchId": None, "clientId": 900001, "orderId": 1,
           "companyName": "Lavandería", "customerName": "Ana",
           "order": {"service": "Lavado y secado", "total": 120}}
ORDER_WORDS = ("120", "ciento veinte", "lavado", "secado")


@dataclass
class Call:
    """What a finished call left behind, for the checks."""
    result: object
    sent: list            # event names the bridge sent (media, clear, mark)
    days: dict            # weekday name → date, from the call's own nextDays
    today: str
    now_time: str

    @property
    def agent_text(self) -> str:
        return " ".join(t["text"] for t in self.result.transcript if t["speaker"] == "agent").lower()

    def caps(self) -> list[str]:
        return [p.get("capability") for p in self.result.proposals]

    def fields(self) -> dict:
        a = self.result.agreement
        return a["fields"] if a else {}


def _valid_slot(c: Call, f: dict) -> list[str]:
    errs = []
    weekday = next((d for d, iso in c.days.items() if iso == f.get("date")), None)
    if weekday is None:
        return [f"date {f.get('date')} not in nextDays"]
    day = next((d for d in OPENING if d.get("weekday") == weekday), None)
    if not day or day.get("closed"):
        return [f"date {f.get('date')} is {weekday}, closed"]
    opens, closes = day["open"], day["close"]
    if not (opens <= f.get("time", "") < closes):
        errs.append(f"time {f.get('time')} outside {weekday} {opens}-{closes}")
    if f.get("date") == c.today and f.get("time", "") <= c.now_time:
        errs.append(f"time {f.get('time')} already passed today")
    return errs


def ended_by_agent(c: Call) -> list[str]:
    return [] if c.result.endedBy == "agent" else [f"endedBy={c.result.endedBy}"]


def agreed(mode: str = "pickup", day: str | None = None, **extra: Callable[[Call, dict], list[str]]):
    """day: the weekday the customer asked for — the agreement's date must be that day."""
    def check(c: Call) -> list[str]:
        f = c.fields()
        if not f:
            return ["no PICKUP_AGREEMENT"]
        errs = [] if f.get("deliveryMode") == mode else [f"deliveryMode={f.get('deliveryMode')}"]
        errs += _valid_slot(c, f)
        if day and f.get("date") != c.days.get(day):
            errs.append(f"date {f.get('date')} is not {day} ({c.days.get(day)})")
        for name, fn in extra.items():
            errs += fn(c, f)
        return errs
    return check


def no_agreement(c: Call) -> list[str]:
    return ["unexpected PICKUP_AGREEMENT"] if "PICKUP_AGREEMENT" in c.caps() else []


def no_order_details(c: Call) -> list[str]:
    said = [w for w in ORDER_WORDS if w in c.agent_text]
    return [f"disclosed order details: {said}"] if said else []


def wants(cap: str):
    return lambda c: [] if cap in c.caps() else [f"no {cap}"]


@dataclass
class Scenario:
    persona: str
    checks: list
    facts: dict = field(default_factory=lambda: FULL)
    barge_in: str | None = None       # said over the agent's opening, without waiting
    # Scripted customer instead of an LLM: (trigger, line) — the line is said once
    # the agent's turn contains `trigger` (None = next turn); otherwise "Sí, correcto."
    script: list | None = None
    voicemail: str | None = None      # played at answer time instead of a person


SCENARIOS: dict[str, Scenario] = {
    "pickup": Scenario(
        "Eres Ana. Te llaman porque tu ropa está lista. Quieres pasar a recogerla el lunes como a las seis de "
        "la tarde y pagas en efectivo.",
        [agreed("pickup", day="lunes"), ended_by_agent]),
    "delivery": Scenario(
        "Eres Ana. Te llaman porque tu ropa está lista. Quieres que te la lleven a tu casa: Calle Reforma 123, "
        "colonia Centro, el lunes a la una de la tarde. Pagas con tarjeta.",
        [agreed("delivery", day="lunes", address=lambda c, f: [] if "reforma" in (f.get("address") or "").lower()
                else [f"address={f.get('address')}"]), ended_by_agent]),
    "another_day": Scenario(
        "Eres Ana. Te llaman porque tu ropa está lista. Hoy y mañana no puedes; quieres pasar a recogerla el "
        "martes en la tarde, como a las cinco. Pagas en efectivo.",
        [agreed("pickup", day="martes"), ended_by_agent]),
    "outside_hours": Scenario(
        "Eres Ana. Te llaman porque tu ropa está lista. Quieres recogerla el domingo a las cinco de la tarde; "
        "si te dicen que no se puede, aceptas la primera hora que te propongan. Pagas en efectivo.",
        [agreed("pickup"), ended_by_agent]),
    "payment_unregistered": Scenario(
        "Eres Ana. Te llaman porque tu ropa está lista. Pasas a recogerla el lunes a las seis de la tarde. "
        "Cuando hablen del pago, preguntas si aceptan vales de despensa; si no, pagas con tarjeta.",
        [agreed("pickup", day="lunes", pay=lambda c, f: ["paymentMethod is vales"] if "vale" in (f.get("paymentMethod") or "").lower()
                else []), ended_by_agent]),
    "payment_unknown": Scenario(
        "Eres Ana. Te llaman porque tu ropa está lista. Pasas a recogerla el lunes a las seis de la tarde. "
        "Cuando hablen del pago, dices que no sabes qué formas de pago aceptan y preguntas cuáles hay; si te "
        "ofrecen que alguien del equipo te contacte, aceptas.",
        [wants("REQUEST_HUMAN"), ended_by_agent], facts=BARE),
    "wrong_person": Scenario(
        "Eres Pedro. Te llaman preguntando por Ana; ese no es tu número y no conoces a ninguna Ana.",
        [no_agreement, no_order_details, ended_by_agent]),
    "human": Scenario(
        "Eres Ana. Te llaman porque tu ropa está lista. Estás molesta: en tu último pedido faltó una prenda y "
        "quieres hablar con una persona del negocio, no con un asistente.",
        [wants("REQUEST_HUMAN"), ended_by_agent]),
    "busy": Scenario(
        "Eres Ana. Estás manejando y no puedes hablar ahora; pides que te manden mensaje.",
        [no_agreement, ended_by_agent]),
    "voicemail": Scenario(
        "", [no_agreement, no_order_details, ended_by_agent],
        voicemail="Hola, has llamado a Ana. No puedo contestar en este momento. Deja tu mensaje después del tono."),
    "interrupt": Scenario(
        "Eres Ana. Te llaman porque tu ropa está lista. Pasas a recogerla el lunes a las seis de la tarde y "
        "pagas en efectivo.",
        [lambda c: [] if "clear" in c.sent else ["no clear sent on barge-in"], agreed("pickup", day="lunes"), ended_by_agent],
        barge_in="¿Bueno? Sí, sí, soy Ana, dígame."),
    "change_after_confirm": Scenario(
        "", [agreed("pickup", day="martes"),
             lambda c: [] if c.caps().count("PICKUP_AGREEMENT") >= 2 else ["change not re-proposed"],
             ended_by_agent],
        script=[(None, "Sí, soy yo."),
                (None, "Paso a recogerla el lunes a las seis de la tarde, y pago en efectivo."),
                ("algo más", "Ay, perdón, mejor el martes a la misma hora."),
                ("martes", "Sí, el martes a las seis, correcto."),
                ("algo más", "No, es todo, gracias.")]),
    "refuses_to_confirm": Scenario(
        "Eres Ana. Te llaman porque tu ropa está lista. Todavía no sabes cuándo vas a poder pasar y no quieres "
        "quedar en ningún día ni hora ahora; lo dices amablemente y no aceptas ninguna propuesta.",
        [no_agreement, ended_by_agent]),
}


def tts_ulaw(text: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "line.wav")
        subprocess.run(["say", "-v", "Paulina", "-o", wav, "--data-format=LEI16@8000", text], check=True)
        with wave.open(wav) as w:
            return pcm16_to_ulaw(w.readframes(w.getnframes()))


class Customer:
    def __init__(self, persona: str, script: list | None = None):
        self.client = genai.Client()
        self.persona = persona
        self.script = list(script) if script else None
        self.history: list[str] = []
        self.done = False

    def reply(self, agent_text: str) -> str | None:
        self.history.append(f"Asistente: {agent_text}")
        if self.script is not None:
            if not self.script:
                return None
            trigger, line = self.script[0]
            if trigger is None or trigger in agent_text.lower():
                self.script.pop(0)
            else:
                line = "Sí, correcto."
            self.history.append(f"Tú: {line}")
            return line
        prompt = (f"{self.persona} Contesta corto y natural, en español de México.\n"
                  "Estás en una llamada telefónica. Conversación hasta ahora:\n" + "\n".join(self.history)
                  + "\n\nResponde SOLO con lo que dices tú en voz alta (una o dos frases). "
                    "Si la llamada ya terminó (el asistente se despidió y tú también), responde exactamente FIN.")
        text = self.client.models.generate_content(model="gemini-2.5-flash", contents=prompt).text.strip()
        if text.upper().startswith("FIN"):
            return None
        self.history.append(f"Tú: {text}")
        return text


async def one_call(port: int, name: str, verbose: bool = True) -> dict:
    sc = SCENARIOS[name]
    token = sign_call_token(PAYLOAD, os.environ["VOICE_CALL_SECRET"])
    customer = Customer(sc.persona, sc.script)
    outgoing = bytearray()
    agent_turns: asyncio.Queue[str] = asyncio.Queue()
    results: list = []
    sent: list[str] = []
    state = {"playback_end": 0.0, "speech_end": None, "latencies": [], "audio_s": 0.0, "first_audio": None}

    async def fixture_facts(company_id, branch_id):
        return sc.facts
    twilio_bridge.fetch_business_facts = fixture_facts
    twilio_bridge.TRANSCRIPT_HOOKS[:] = [lambda who, text: who == "agent" and agent_turns.put_nowait(text)]
    twilio_bridge.RESULT_HOOKS[:] = [results.append]
    ctx = twilio_bridge.build_call_context(PAYLOAD, twilio_bridge.hermosillo_now())

    def say(text: str):
        if verbose:
            print(f"  CLIENTE: {text}")
        outgoing.extend(tts_ulaw(text))

    async with websockets.connect(f"ws://127.0.0.1:{port}/voice/twilio-stream") as ws:
        await ws.send(json.dumps({"event": "connected"}))
        await ws.send(json.dumps({"event": "start", "start": {
            "streamSid": "MZsim", "callSid": f"CAsim-{name}-{int(time.time())}",
            "customParameters": {"token": token}}}))
        started = time.monotonic()
        if sc.voicemail:
            customer.done = True
            await asyncio.to_thread(say, sc.voicemail)

        async def sender():
            while True:
                if outgoing:
                    chunk = bytes(outgoing[:FRAME]); del outgoing[:FRAME]
                    if not outgoing:
                        state["speech_end"] = time.monotonic()
                else:
                    chunk = SILENCE
                await ws.send(json.dumps({"event": "media", "media": {"payload": base64.b64encode(chunk).decode()}}))
                await asyncio.sleep(0.02)

        async def receiver():
            async for raw in ws:
                frame = json.loads(raw)
                now = time.monotonic()
                sent.append(frame["event"])
                if frame["event"] == "media":
                    n = len(base64.b64decode(frame["media"]["payload"]))
                    if state["speech_end"] is not None:
                        state["latencies"].append(now - state["speech_end"])
                        state["speech_end"] = None
                    state["first_audio"] = state["first_audio"] or now
                    state["playback_end"] = max(state["playback_end"], now) + n / 8000
                    state["audio_s"] += n / 8000
                elif frame["event"] == "clear":
                    state["playback_end"] = now
                elif frame["event"] == "mark":
                    await asyncio.sleep(max(0.0, state["playback_end"] - now))
                    await ws.send(json.dumps({"event": "mark", "mark": frame["mark"]}))

        async def barge_in():
            while state["first_audio"] is None:
                await asyncio.sleep(0.05)
            await asyncio.sleep(2.0)            # two seconds into the agent's opening
            customer.history.append(f"Tú: {sc.barge_in}")
            await asyncio.to_thread(say, sc.barge_in)

        async def conversation():
            skip_first = sc.barge_in is not None
            while True:
                agent_text = await agent_turns.get()
                if verbose:
                    print(f"  AGENTE : {agent_text}")
                if customer.done or (skip_first and len(customer.history) <= 1):
                    if skip_first:
                        customer.history.insert(0, f"Asistente: {agent_text}")
                        skip_first = False
                    continue
                await asyncio.sleep(max(0.0, state["playback_end"] - time.monotonic()) + 0.4)
                line = await asyncio.to_thread(customer.reply, agent_text)
                if line is None:
                    customer.done = True       # stays on the line, silent
                    continue
                await asyncio.to_thread(say, line)

        tasks = [asyncio.create_task(t()) for t in (sender, receiver, conversation)]
        if sc.barge_in:
            tasks.append(asyncio.create_task(barge_in()))
        try:
            await asyncio.wait_for(tasks[1], 7 * 60)    # ends when the bridge closes the socket
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass
        finally:
            for t in tasks:
                t.cancel()

    for _ in range(50):
        if results:
            break
        await asyncio.sleep(0.1)
    if not results:
        return {"scenario": name, "pass": False, "failures": ["no CallResult"], "latencies": [], "seconds": 0}
    call = Call(result=results[0], sent=sent, today=ctx["today"], now_time=ctx["nowTime"],
                days={d["weekday"]: d["date"] for d in reversed(ctx["nextDays"])})
    failures = [e for check in sc.checks for e in check(call)]
    return {"scenario": name, "pass": not failures, "failures": failures,
            "seconds": round(time.monotonic() - started, 1), "agentAudioS": round(state["audio_s"], 1),
            "latencies": [round(x, 2) for x in state["latencies"]],
            "endedBy": call.result.endedBy, "bargeIns": call.result.bargeIns,
            "proposals": call.caps(), "agreement": call.fields(), "tokens": call.result.tokens,
            "toolTrace": call.result.toolTrace, "repeatedQuestions": call.result.repeatedQuestions,
            "promptVersion": call.result.promptVersion}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="pickup", help=f"one of {list(SCENARIOS)}, comma-separated, or all")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--json", help="write every call's summary to this file")
    ap.add_argument("--company-id", type=int, default=PAYLOAD["companyId"],
                    help="company whose registered opening hours the calls use")
    args = ap.parse_args()
    PAYLOAD["companyId"] = args.company_id
    await load_opening_hours(args.company_id)
    names = list(SCENARIOS) if args.scenario == "all" else args.scenario.split(",")

    app = FastAPI()
    app.include_router(twilio_bridge.router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    summaries = []
    for name in names:
        for run in range(args.runs):
            print(f"\n=== {name} #{run + 1} ===")
            s = await one_call(args.port, name)
            summaries.append(s)
            print("  →", "PASS" if s["pass"] else f"FAIL {s['failures']}",
                  json.dumps({k: s.get(k) for k in ("seconds", "latencies", "bargeIns", "proposals", "agreement", "toolTrace")},
                             ensure_ascii=False))
            if args.json:
                Path(args.json).write_text(json.dumps(summaries, ensure_ascii=False, indent=1))

    server.should_exit = True
    await serve
    print("\n=== MATRIX ===")
    all_lat = [x for s in summaries for x in s["latencies"]]
    for name in names:
        runs = [s for s in summaries if s["scenario"] == name]
        lat = [x for s in runs for x in s["latencies"]]
        fails = sorted({f for s in runs for f in s["failures"]})
        print(f"{name:22} {sum(s['pass'] for s in runs)}/{len(runs)}  "
              f"latency max={max(lat, default=0):.2f}s  {'; '.join(fails)}")
    print(f"TOTAL {sum(s['pass'] for s in summaries)}/{len(summaries)}  "
          f"latency avg={sum(all_lat) / len(all_lat) if all_lat else 0:.2f}s max={max(all_lat, default=0):.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
