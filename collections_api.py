"""
Loan collections endpoints (included by main.py). Server-to-server only.

    POST /collections/reminder-preview   the reminder for one loan — sends NOTHING
    POST /collections/whatsapp           one borrower message → reply + proposal

Off unless COLLECTIONS_ENABLED=1, and then only with X-Worker-Key: they expose a
borrower's debt, so they fail closed when no key is configured.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part
from pydantic import BaseModel, Field

from agents.collections_whatsapp import collections_whatsapp_agent
from config import settings
from loan_collections.handoff import SAYS_PAID, request_human
from loan_collections.reminders import build_reminder, may_contact
from loan_collections.situation import fetch_situation, hermosillo_now, next_days
from metering import MeteredRunner

APP_NAME = "loan_agents_collections_whatsapp"
_IDLE_SECONDS = 2 * 60 * 60
FALLBACK = "Perdón, no pude responderte en este momento. Alguien del equipo lo va a revisar."

# Decided in code, not by the model: in live runs it said "te contactará alguien"
# without recording the handoff (13/24).
_WRONG_NUMBER = re.compile(r"n[uú]mero equivocado|equivocad[oa] de n[uú]mero|no soy (esa|ese|la|el|quien)|"
                           r"no conozco a|se equivoc", re.I)
_STOP = re.compile(r"(no|dejen? de|ya no|nunca|basta de)\s+(me\s+|quiero que me\s+|vuelvan? a\s+)*"
                   r"(escrib|llam|mand|contact|molest)|^\s*(stop|baja|alto)\s*[.!]*\s*$", re.I)
_PROMISES_PERSON = re.compile(
    r"(alguien|integrante|miembro|asesor|compa[ñn]er|persona|colega|equipo)[^?]*"
    r"(contact|comuni|revis|llam|seguimiento|busc|ayud|escrib)", re.I)
_REPLY_WRONG = "Disculpa la molestia. Le aviso al equipo para que revise este número."
_REPLY_STOP = "Entendido, le paso tu solicitud al equipo para que la atienda."


def fixed_answer(message: str, first_name: str) -> dict | None:
    """Messages answered without the model."""
    not_them = re.search(rf"\bno soy {re.escape(first_name)}\b", message, re.I)
    if not_them or _WRONG_NUMBER.search(message):
        return {"reply": _REPLY_WRONG, "pendingAction": request_human("número equivocado")}
    if _STOP.search(message):
        return {"reply": _REPLY_STOP, "pendingAction": request_human("pide no ser contactado")}
    return None


def ensure_handoff(message: str, reply: str, proposal: dict | None, situation: dict) -> dict | None:
    if (proposal or {}).get("capability") == "REQUEST_HUMAN":
        return proposal
    if SAYS_PAID.search(message):       # wins over a promise: never collect what may be paid
        return request_human("dice que ya pagó")
    if proposal:
        return proposal
    if situation.get("status") == "hold":
        return request_human("situación del préstamo en revisión")
    if _PROMISES_PERSON.search(reply):
        return request_human("el asistente ofreció atención de una persona")
    return None


_sessions = InMemorySessionService()
_runner = MeteredRunner(agent=collections_whatsapp_agent, app_name=APP_NAME, session_service=_sessions)


def _authorized(x_worker_key: str = Header(default="")) -> None:
    if os.environ.get("COLLECTIONS_ENABLED") != "1" or not settings.WORKER_KEY:
        raise HTTPException(503, "collections is disabled")
    if not hmac.compare_digest(x_worker_key.encode(), settings.WORKER_KEY.encode()):
        raise HTTPException(401, "unauthorized")


router = APIRouter(prefix="/collections", dependencies=[Depends(_authorized)])


class _Loan(BaseModel):
    companyId: int
    clientId: int
    loanId: int
    companyName: str = Field(max_length=120)
    firstName: str = Field(max_length=60)


class ReminderRequest(_Loan):
    contactsToday: int      # required on purpose: unknown history must not read as zero
    optedOut: bool


class WhatsAppRequest(_Loan):
    phone: str = Field(min_length=10, max_length=20)   # the channel's verified sender
    message: str = Field(min_length=1, max_length=2000)


async def _situation(req: _Loan) -> dict:
    return await asyncio.to_thread(fetch_situation, req.companyId, req.clientId, req.loanId)


@router.post("/reminder-preview")
async def reminder_preview(req: ReminderRequest) -> dict:
    s = await _situation(req)
    blocked = may_contact(hermosillo_now(), req.contactsToday, req.optedOut)
    if s["status"] == "hold":
        blocked = blocked or s["reason"]
    reminder = None if blocked else build_reminder(s, req.firstName, req.companyName)
    return {"sent": False, "blockedBy": blocked, "status": s["status"], "reminder": reminder}


@router.post("/whatsapp")
async def whatsapp(req: WhatsAppRequest) -> dict:
    fixed = fixed_answer(req.message, req.firstName)
    if fixed:
        return fixed
    # loanId in the key: one loan's chat never continues into another's.
    user_id = f"collections-{req.companyId}-{req.clientId}-{req.loanId}-{req.phone}"
    key = {"app_name": APP_NAME, "user_id": user_id, "session_id": user_id}
    session = await _sessions.get_session(**key)
    if session is not None and time.time() - session.last_update_time > _IDLE_SECONDS:
        await _sessions.delete_session(**key)
        session = None
    if session is None:
        session = await _sessions.create_session(**key, state={})
    s = await _situation(req)       # fresh every turn: they may have just paid
    today = hermosillo_now().date()
    context = {"companyName": req.companyName, "customerFirstName": req.firstName,
               "today": today.isoformat(), "nextDays": next_days(today)}
    message = Content(role="user", parts=[Part(
        text=f"{req.message}\n\nContext: {json.dumps(context, ensure_ascii=False)}")])
    proposal, reply = None, ""
    for _ in range(2):
        texts = []
        async for event in _runner.run_async(user_id=user_id, session_id=user_id, new_message=message,
                                             state_delta={"coll_situation": s}):
            delta = event.actions.state_delta if event.actions else {}
            proposal = delta.get("pending_action") or proposal      # this turn's only
            if not event.partial and event.content and event.content.role == "model":
                text = "".join(p.text or "" for p in event.content.parts or []
                               if not getattr(p, "thought", False)).strip()
                if text:
                    texts.append(text)
        reply = "\n\n".join(texts)
        if reply:
            break
        message = Content(role="user", parts=[Part(
            text="(Mensaje interno del sistema: tu respuesta quedó vacía. Responde al último mensaje del cliente.)")])
    if not reply:
        reply = FALLBACK
        proposal = proposal or request_human("respuesta vacía del agente")
    proposal = ensure_handoff(req.message, reply, proposal, s)
    return {"reply": reply, "pendingAction": proposal}
