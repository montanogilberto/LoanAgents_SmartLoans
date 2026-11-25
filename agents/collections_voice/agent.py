"""Collections Voice Agent — copy of voice_secretary for collections calls
(voice/collections_bridge.py). Independent: changing one never changes the other.

Uses a Gemini Live (native audio) model: real-time speech needs bidirectional
streaming, which gemini-2.5-flash's request/response API doesn't do. The model
is configurable with VOICE_MODEL so latency/quality can be compared.
"""
import asyncio
import contextlib
import difflib
import os
import re
import time
import unicodedata

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

from agents.collections_voice import live_state
from agents.collections_voice.prompt import INSTRUCTION
from loan_collections.handoff import SAYS_PAID, request_human
from loan_collections.situation import check_promise, hermosillo_now
from tools.pending_actions import propose_action as _propose_action

VOICE_MODEL = os.environ.get("VOICE_MODEL", "gemini-2.5-flash-native-audio-latest")
# Thinking adds dead air before each answer on the phone. "" = model default.
VOICE_THINKING_BUDGET = os.environ.get("VOICE_THINKING_BUDGET", "0")


class LiveGemini(Gemini):
    """Gemini for live calls: ADK copies only some RunConfig fields into the Live
    connect config (not thinking), so the thinking budget is set here."""

    thinking_budget: int | None = None

    @contextlib.asynccontextmanager
    async def connect(self, llm_request):
        if self.thinking_budget is not None:
            llm_request.live_connect_config.thinking_config = types.ThinkingConfig(
                thinking_budget=self.thinking_budget)
        async with super().connect(llm_request) as connection:
            yield connection


_NOT_SPEECH = re.compile(r"<[^>]*>")


def _plain(text: str) -> list[str]:
    text = unicodedata.normalize("NFD", _NOT_SPEECH.sub(" ", (text or "").lower()))
    return re.findall(r"[a-z]+", "".join(c for c in text if not unicodedata.combining(c)))


def _says_full_name(heard: str, full_name: str | None) -> bool:
    """They said the first name AND a surname. The surname is never told to the
    model, so it can't be fed to whoever answers: a "sí" alone isn't enough here."""
    expected, said = _plain(full_name), _plain(heard)
    if len(expected) < 2 or re.search(r"\bno soy\b|equivocad|no vive|no se encuentra|no esta\b(?! bien)", " ".join(said)):
        return False

    def has(word):
        return any(difflib.SequenceMatcher(None, word, w).ratio() >= 0.8 for w in said)
    return has(expected[0]) and any(has(w) for w in expected[1:] if len(w) > 3)


IDENTITY_WINDOW_S = 8.0     # the customer's reply to "¿Hablo con …?"
_TRANSCRIPT_WAIT_S = 0.6    # input transcription arrives after the audio


SAY_PAID = "Gracias por avisarnos. El equipo va a revisar su pago. ¿Hay algo más en lo que le pueda ayudar?"
SAY_PROMISE = "Queda anotado para que el equipo lo revise. ¿Hay algo más en lo que le pueda ayudar?"


def spoken_loan(s: dict) -> dict:
    """Spanish keys and a ready sentence: with the raw dict the agent said "un pago overdue"."""
    if s.get("status") != "overdue":
        return {}
    n, d, amount = s["overdueCount"], s["daysLate"], s["overdueAmount"]
    pesos = f"{amount:,.0f} pesos" if amount == int(amount) else f"{amount:,.2f} pesos"
    return {"montoVencido": amount,
            "say": f"Tiene {n} {'pago vencido' if n == 1 else 'pagos vencidos'} por {pesos}, "
                   f"con {d} {'día' if d == 1 else 'días'} de atraso. ¿Cuándo podría realizar su pago?"}


async def confirm_customer_identity(what_they_said: str, tool_context: ToolContext) -> dict:
    """Call this when the person has told you their full name. Returns their loan
    situation — you don't have it before, so a wrong person can't hear it.

    Args:
        what_they_said: Their exact words (e.g. "Ana Pérez López").

    Returns:
        {"confirmed": true, "loan": {...}}, {"confirmed": true, "alreadyPaid": true} or {"confirmed": false, "error": ...}.
    """
    name = tool_context.state.get("coll_customer_name")
    session_id = live_state.session_id_of(tool_context)
    if live_state.is_live(session_id):
        # On a live call the evidence is what the bridge heard, not the model's account.
        # Transcription arrives in fragments ("Soy Ana" … "Hernández López"): wait for a
        # match, not for the first fragment. The whole call counts: the surname is never
        # said by the agent, so wherever it was heard it came from the customer.
        deadline = time.monotonic() + _TRANSCRIPT_WAIT_S
        while True:
            heard = live_state.customer_text_since(session_id, 0.0)
            confirmed = _says_full_name(heard, name)
            if confirmed or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)
    else:
        confirmed = _says_full_name(what_they_said, name)
    waits = tool_context.state.get("coll_identity_waits") or 0
    if not confirmed and live_state.is_live(session_id) and waits < 2:
        # The transcript can lag behind the audio, and no event arrives while a tool
        # runs: answering and being called again is what lets it through.
        tool_context.state["coll_identity_waits"] = waits + 1
        return {"confirmed": False, "retry": True, "say": "Un momento, por favor.",
                "instruction": "Di eso y llama de nuevo confirm_customer_identity; aún no digas que no coincide."}
    if not confirmed:
        return {"confirmed": False,
                "error": "No coincide el nombre completo. No digas el motivo de la llamada."}
    tool_context.state["voice_identity_confirmed"] = True
    if SAYS_PAID.search(live_state.customer_text_since(session_id, 0.0) if session_id else what_they_said):
        # They said so before we asked: no amount is told and nothing is collected.
        tool_context.state["pending_action"] = request_human("dice que ya pagó")
        return {"confirmed": True, "alreadyPaid": True, "say": SAY_PAID}
    return {"confirmed": True, "loan": spoken_loan(tool_context.state.get("coll_situation") or {})}


def propose_action(capability: str, fields: dict, confirmation_summary: str, tool_context: ToolContext) -> dict:
    """Records a proposal from this call. It writes nothing.

    Args:
        capability: "PROMISE_TO_PAY" or "REQUEST_HUMAN".
        fields: PROMISE_TO_PAY: amount (number), weekday, date (from nextDays).
            REQUEST_HUMAN: reason.
        confirmation_summary: A short summary.

    Returns:
        {"proposed": true}, or {"proposed": false, "errors": [...]}.
    """
    if capability not in ("PROMISE_TO_PAY", "REQUEST_HUMAN"):
        return {"proposed": False, "errors": ["capability must be PROMISE_TO_PAY or REQUEST_HUMAN"]}
    if capability == "PROMISE_TO_PAY":
        if not tool_context.state.get("voice_identity_confirmed"):
            return {"proposed": False, "errors": ["identity is not confirmed"]}
        if SAYS_PAID.search(live_state.customer_text_since(live_state.session_id_of(tool_context), 0.0)):
            return {"proposed": False, "errors": ["the customer said they already paid: use REQUEST_HUMAN"]}
        errors = check_promise(fields or {}, tool_context.state.get("coll_situation") or {}, hermosillo_now().date())
        if errors:
            return {"proposed": False, "errors": errors}
    out = _propose_action(capability, fields, confirmation_summary, tool_context)
    if capability == "PROMISE_TO_PAY" and out.get("proposed"):
        live_state.mark_proposal(live_state.session_id_of(tool_context))
        out = {**out, "say": SAY_PROMISE}
    return out


def end_call(tool_context: ToolContext) -> dict:
    """Hangs up the call. Call it only AFTER you've said goodbye — the line is
    closed as soon as your goodbye has finished playing.

    Returns:
        {"ending": true}
    """
    session_id = live_state.session_id_of(tool_context)
    if live_state.is_live(session_id) and not live_state.may_end_call(session_id):
        # An agreement isn't the end of the call: the customer gets one chance to
        # add or change something (change_after_confirm failed 0/3 without it).
        return {"ending": False,
                "error": "Antes de despedirte pregunta \"¿Hay algo más en lo que le pueda ayudar?\" "
                         "y espera su respuesta."}
    state = tool_context.state
    if (state.get("voice_identity_confirmed") and not state.get("pending_action")
            and not state.get("coll_end_held")):
        # A live run agreed on Friday and hung up without proposing it. Held once only:
        # a customer who commits to nothing must still be able to end the call.
        state["coll_end_held"] = True
        return {"ending": False,
                "error": "No registraste nada. Si el cliente dio una fecha, llama ahora propose_action "
                         "PROMISE_TO_PAY; si dijo que ya pagó, no puede pagar, no reconoce o pidió algo, "
                         "REQUEST_HUMAN. No repitas la despedida: después llama end_call otra vez. "
                         "Si no dijo nada de eso, llama end_call otra vez."}
    tool_context.state["voice_end_call"] = True
    return {"ending": True}


collections_voice_agent = Agent(
    name="collections_voice_agent",
    description=("Live collections call: confirms the borrower's identity, explains the overdue "
                 "situation and PROPOSES a PROMISE_TO_PAY or REQUEST_HUMAN. Never confirms a payment."),
    model=LiveGemini(model=VOICE_MODEL,
                     thinking_budget=int(VOICE_THINKING_BUDGET) if VOICE_THINKING_BUDGET else None),
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=propose_action), FunctionTool(func=confirm_customer_identity),
           FunctionTool(func=end_call)],
)
