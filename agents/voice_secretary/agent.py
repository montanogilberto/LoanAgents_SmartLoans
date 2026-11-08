"""Voice Secretary Agent — runs inside a live phone call (voice/twilio_bridge.py).

Uses a Gemini Live (native audio) model: real-time speech needs bidirectional
streaming, which gemini-2.5-flash's request/response API doesn't do. The model
is configurable with VOICE_MODEL so latency/quality can be compared.
"""
import asyncio
import contextlib
import inspect
import os
import re
import time

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

from agents.voice_secretary import live_state
from agents.voice_secretary.calendar_check import check_agreement_slot
from agents.voice_secretary.prompt import INSTRUCTION
from tools.backend_api import get_faqs
from tools.pending_actions import propose_action as _propose_action
from tools.safe_tool import never_raise

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


# A clear "it's me". "¿Bueno?" alone is how Mexicans answer the phone, not a yes;
# noise was taken as a yes on a live call (2026-11-07).
_AFFIRMATIVE = re.compile(
    r"\b(s[ií]|soy yo|yo soy|[eé]l habla|ella habla|con [eé]l|con ella|a sus [oó]rdenes|"
    r"d[ií]game|mande|as[ií] es|correcto|claro|exacto|efectivamente|ajá|aja)\b")
_NOT_SPEECH = re.compile(r"<[^>]*>")


def _is_clear_yes(what_they_said: str, customer_name: str | None) -> bool:
    text = _NOT_SPEECH.sub(" ", (what_they_said or "").lower()).strip(" .,¿?¡!")
    if not text or re.search(r"\bno\b|equivoc|no es aqu|no vive", text):
        return False
    first = (customer_name or "").split()[0].lower() if customer_name else ""
    return bool(_AFFIRMATIVE.search(text) or (first and first in text))


IDENTITY_WINDOW_S = 8.0     # the customer's reply to "¿Hablo con …?"
_TRANSCRIPT_WAIT_S = 1.0    # input transcription arrives after the audio


async def confirm_customer_identity(what_they_said: str, tool_context: ToolContext) -> dict:
    """Call this when the person on the line says they ARE the customer you
    asked for. Returns their order — you don't have it before, so nothing
    about it can reach a wrong person or a voicemail.

    Args:
        what_they_said: Their exact words that confirm it (e.g. "Sí, soy yo").
            Noise, silence or just "¿Bueno?" is NOT a confirmation.

    Returns:
        {"confirmed": true, "order": {"service": str|null, "total": number|null}}
        or {"confirmed": false, "error": ...} → ask again, clearly, if you're
        speaking with the customer by name; say nothing about the order.
    """
    name = tool_context.state.get("voice_customer_name")
    session_id = live_state.session_id_of(tool_context)
    if live_state.is_live(session_id):
        # On a live call the evidence is what the bridge heard, not the model's account.
        heard, deadline = "", time.monotonic() + _TRANSCRIPT_WAIT_S
        while True:
            heard = live_state.customer_text_since(session_id, time.monotonic() - IDENTITY_WINDOW_S)
            if _NOT_SPEECH.sub("", heard).strip() or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)
        confirmed = _is_clear_yes(heard, name)
    else:
        confirmed = _is_clear_yes(what_they_said, name)
    if not confirmed:
        return {"confirmed": False,
                "error": "No se escuchó una confirmación clara. Pide amablemente que lo repita "
                         "(¿hablo con …?) y no menciones el pedido."}
    tool_context.state["voice_identity_confirmed"] = True
    return {"confirmed": True, "order": tool_context.state.get("voice_order") or {}}


def propose_action(capability: str, fields: dict, confirmation_summary: str, tool_context: ToolContext) -> dict:
    """Records a proposed agreement for this call. Call it ONLY when the
    customer has confirmed every field — never with a guessed value.

    Args:
        capability: "PICKUP_AGREEMENT" or "REQUEST_HUMAN".
        fields: The agreed fields. PICKUP_AGREEMENT needs weekday (the day the
            customer agreed to, e.g. "lunes") AND its date from nextDays.
        confirmation_summary: A short summary of what was agreed.

    Returns:
        {"proposed": true} on success.
        {"proposed": false, "errors": [...]} when a field is wrong — e.g. the
        date isn't that weekday, or the time is outside the opening hours. Fix
        it (the error names the correct date) or ask the customer, then call
        this again. Never tell the customer it's done while there are errors.
    """
    if capability == "PICKUP_AGREEMENT":
        errors = check_agreement_slot(fields or {}, tool_context.state.get("voice_calendar"))
        if (fields or {}).get("deliveryMode") == "delivery" and not tool_context.state.get("voice_delivery_allowed"):
            errors.append("this business has no home delivery registered: only pickup can be agreed")
        if errors:
            return {"proposed": False, "errors": errors}
    out = _propose_action(capability, fields, confirmation_summary, tool_context)
    if capability == "PICKUP_AGREEMENT" and out.get("proposed"):
        live_state.mark_proposal(live_state.session_id_of(tool_context))
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
    tool_context.state["voice_end_call"] = True
    return {"ending": True}


def _use_trusted_ids(tool, args, tool_context):
    """company_id / branch_id come from the signed call token (set in session
    state by the bridge), never from the model."""
    func = getattr(tool, "func", None)
    if func is None:
        return None
    params = inspect.signature(func).parameters
    for arg, state_key in (("company_id", "voice_company_id"), ("branch_id", "voice_branch_id")):
        if arg in params:
            args[arg] = tool_context.state.get(state_key)
    return None


voice_secretary_agent = Agent(
    name="voice_secretary_agent",
    description=(
        "Live phone assistant: calls a customer whose order is ready, agrees how "
        "and when they'll get it (pickup or delivery, day/time, address, payment) "
        "and PROPOSES a PICKUP_AGREEMENT — the backend confirms it with the "
        "customer by WhatsApp. Answers business facts only from tools."
    ),
    model=LiveGemini(model=VOICE_MODEL,
                     thinking_budget=int(VOICE_THINKING_BUDGET) if VOICE_THINKING_BUDGET else None),
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        # Business info and opening hours are already in the call context
        # (voice/twilio_bridge.py reads them when the call connects).
        FunctionTool(func=never_raise(get_faqs)),
        FunctionTool(func=propose_action),
        FunctionTool(func=confirm_customer_identity),
        FunctionTool(func=end_call),
    ],
    before_tool_callback=_use_trusted_ids,
)
