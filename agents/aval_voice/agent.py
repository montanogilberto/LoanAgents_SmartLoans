"""Aval secretary — live call to a proposed guarantor (voice/aval_bridge.py).

Complementary evidence only: a call NEVER makes someone an aval (credit assessment
PRD, AC5). The order of the questions, the identity and every "sí" are checked in
code against what the bridge heard, not against the model's account.
Independent from the other voice agents, like collections_voice.
"""
import asyncio
import contextlib
import os
import time

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

from agents.aval_voice import live_state, rules
from agents.aval_voice.prompt import INSTRUCTION
from loan_collections.handoff import request_human as _human
from tools.pending_actions import propose_action as _propose_action
from voice.live_tools import deliver_tool_calls_now

VOICE_MODEL = os.environ.get("VOICE_MODEL", "gemini-2.5-flash-native-audio-latest")
VOICE_THINKING_BUDGET = os.environ.get("VOICE_THINKING_BUDGET", "0")   # thinking = dead air

_TRANSCRIPT_WAIT_S = 1.5    # input transcription arrives after the audio
_MAX_WAITS = 1
QUESTIONS = ("relationship", "understands", "decision")


class LiveGemini(Gemini):
    """ADK doesn't copy the thinking budget into the Live connect config."""

    thinking_budget: int | None = None

    @contextlib.asynccontextmanager
    async def connect(self, llm_request):
        if self.thinking_budget is not None:
            llm_request.live_connect_config.thinking_config = types.ThinkingConfig(
                thinking_budget=self.thinking_budget)
        async with super().connect(llm_request) as connection:
            yield deliver_tool_calls_now(connection, live_state.hear)


async def _heard(session_id, since: float, fallback: str, ok) -> str:
    """What the person said since `since`, waiting a moment until ok(text)."""
    if not live_state.is_live(session_id):
        return fallback
    deadline = time.monotonic() + _TRANSCRIPT_WAIT_S
    while True:
        text = live_state.customer_text_since(session_id, since)
        if ok(text) or time.monotonic() >= deadline:
            return text
        await asyncio.sleep(0.2)


def _may_wait(state, key: str, session_id) -> bool:
    waits = state.get(key) or 0
    if not live_state.is_live(session_id) or waits >= _MAX_WAITS:
        return False
    state[key] = waits + 1
    return True


async def confirm_guarantor_identity(what_they_said: str, tool_context: ToolContext) -> dict:
    """Call this when the person has told you their full name. Returns why you're
    calling — you don't have it before, so a wrong person can't hear it.

    Args:
        what_they_said: Their exact words (e.g. "Ana Pérez López").

    Returns:
        {"confirmed": true, "say": ...} or {"confirmed": false, "error": ...}.
    """
    state = tool_context.state
    name, sid = state.get("aval_guarantor_name"), live_state.session_id_of(tool_context)
    # The whole call counts: the agent never says the surname.
    heard = await _heard(sid, 0.0, what_they_said, lambda t: rules.says_full_name(t, name))
    if not rules.says_full_name(heard, name):
        if _may_wait(state, "aval_identity_waits", sid):
            return {"confirmed": False, "retry": True, "say": "Un momento, por favor.",
                    "instruction": "Di eso y llama de nuevo confirm_guarantor_identity; aún no digas que no coincide."}
        return {"confirmed": False, "error": "No coincide el nombre completo. No digas el motivo de la llamada."}
    state["voice_identity_confirmed"] = True
    live_state.mark_question(sid)
    return {"confirmed": True, "say": rules.say_why(state.get("aval_applicant_name") or "")}


async def record_answer(question: str, answer: str, their_words: str, tool_context: ToolContext) -> dict:
    """Records the person's answer to one question and returns what to say next.

    Args:
        question: "relationship", "understands" or "decision".
        answer: "yes", "no" or "unsure". For relationship: whether they know the applicant.
        their_words: Their exact words; for relationship, include how they're related.

    Returns:
        {"recorded": true, "say": ...} or {"recorded": false, "error": ...}.
    """
    state, sid = tool_context.state, live_state.session_id_of(tool_context)
    if not state.get("voice_identity_confirmed"):
        return {"recorded": False, "error": "identity is not confirmed"}
    if question not in QUESTIONS or answer not in ("yes", "no", "unsure"):
        return {"recorded": False, "error": f"question must be one of {QUESTIONS}; answer yes, no or unsure"}
    done = dict(state.get("aval_answers") or {})
    if question == "understands" and done.get("relationship") != "yes":
        return {"recorded": False, "error": "ask and record relationship first"}
    if question == "decision" and answer != "no" and done.get("understands") != "yes":
        return {"recorded": False, "error": "ask and record understands first"}
    if state.get("aval_outcome") and question != "decision":
        return {"recorded": False, "error": "already recorded"}

    since = live_state.question_asked_at(sid)
    need_yes = answer == "yes" and question != "relationship"
    ok = rules.clear_yes if need_yes else (lambda t: bool(rules.words(t)))
    heard = await _heard(sid, since, their_words, ok)
    if not ok(heard):
        if not rules.words(heard) and _may_wait(state, f"aval_waits_{question}", sid):
            return {"recorded": False, "retry": True, "say": "Un momento, por favor.",
                    "instruction": "Di eso y llama de nuevo record_answer con lo mismo."}
        if not state.get(f"aval_reasked_{question}"):
            state[f"aval_reasked_{question}"] = True
            live_state.mark_question(sid)
            return {"recorded": False, "error": "No se escuchó un sí claro o la respuesta. Repite la pregunta una sola vez, "
                                                "sin sugerir la respuesta, y espera."}
        answer = "unsure"

    # Never "yes" on an unclear second answer: below it can only end in UNSURE.
    done[question] = answer
    state["aval_answers"] = done
    if question == "relationship":
        state["aval_relationship"] = rules.relationship_check(
            state.get("aval_declared_relationship"), f"{heard} {their_words}")
        if answer == "no":
            return _finish(rules.DOES_NOT_KNOW, tool_context)
        if answer == "unsure":
            return _finish(rules.UNSURE, tool_context)
        live_state.mark_question(sid)
        return {"recorded": True, "say": rules.say_obligation(
            (state.get("aval_applicant_name") or "").split(" ")[0])}
    if question == "understands":
        if answer != "yes":
            if not state.get("aval_explained_twice"):
                state["aval_explained_twice"] = True
                live_state.mark_question(sid)
                return {"recorded": True, "say": rules.say_obligation(
                    (state.get("aval_applicant_name") or "").split(" ")[0])}
            return _finish(rules.UNSURE, tool_context)
        live_state.mark_question(sid)
        return {"recorded": True, "say": rules.SAY_DECISION}
    return _finish({"yes": rules.ACCEPTS, "no": rules.DECLINES}.get(answer, rules.UNSURE), tool_context)


def _finish(outcome: str, tool_context: ToolContext) -> dict:
    state = tool_context.state
    done = state.get("aval_answers") or {}
    out = _propose_action("GUARANTOR_CALL_RESULT", {
        "outcome": outcome,
        "knowsApplicant": done.get("relationship", "unsure"),
        "relationshipCheck": state.get("aval_relationship") or "UNCLEAR",
        "understandsObligation": done.get("understands", "unsure"),
    }, "Resultado de la llamada al aval", tool_context)
    if not out.get("proposed"):
        return {"recorded": False, "error": "; ".join(out.get("errors") or [])}
    state["aval_outcome"] = outcome
    sid = live_state.session_id_of(tool_context)
    live_state.mark_question(sid)
    if outcome != rules.DOES_NOT_KNOW:      # that sentence already closes the call
        live_state.mark_proposal(sid)
    return {"recorded": True, "say": rules.SAY_RESULT[outcome]}


def request_human(reason: str, tool_context: ToolContext) -> dict:
    """Passes this case to a person of the team.

    Args:
        reason: Short reason, in Spanish.

    Returns:
        {"proposed": true}
    """
    tool_context.state["aval_handoff"] = True
    return _propose_action("REQUEST_HUMAN", {"reason": reason or "pide hablar con una persona"},
                           _human("")["confirmationSummary"], tool_context)


def end_call(tool_context: ToolContext) -> dict:
    """Hangs up the call. Call it only AFTER you've said goodbye — the line is
    closed as soon as your goodbye has finished playing.

    Returns:
        {"ending": true}
    """
    sid, state = live_state.session_id_of(tool_context), tool_context.state
    recorded = state.get("aval_outcome") or state.get("aval_handoff")
    if not state.get("aval_handoff") and live_state.is_live(sid) and not live_state.may_end_call(sid):
        return {"ending": False, "error": "Antes de despedirte pregunta \"¿Tiene alguna duda o comentario?\" "
                                          "y espera su respuesta."}
    if state.get("voice_identity_confirmed") and not recorded and not state.get("aval_end_held"):
        state["aval_end_held"] = True      # held once: the person can always leave
        return {"ending": False, "error": "No registraste nada. Si la persona respondió, llama record_answer; si no quiso "
                                          "seguir o está ocupada, request_human. No repitas la despedida: "
                                          "después llama end_call otra vez."}
    state["voice_end_call"] = True
    return {"ending": True}


aval_voice_agent = Agent(
    name="aval_voice_agent",
    description=("Live call to a proposed aval: confirms identity, relationship and free will, and PROPOSES "
                 "a GUARANTOR_CALL_RESULT or REQUEST_HUMAN. Never makes anyone an aval."),
    model=LiveGemini(model=VOICE_MODEL,
                     thinking_budget=int(VOICE_THINKING_BUDGET) if VOICE_THINKING_BUDGET else None),
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=confirm_guarantor_identity), FunctionTool(func=record_answer),
           FunctionTool(func=request_human), FunctionTool(func=end_call)],
)
