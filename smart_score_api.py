"""
smartScore — payment capacity (included by main.py). Server-to-server only.

    POST /smart-score/capacity   amounts + policy → factors, capacity, level, es-MX explanation

Off unless SMART_SCORE_ENABLED=1, and then only with X-Worker-Key: the verified income comes
from smartloans_backend (customerIncomeVerifications), never from the applicant's device.
The rule is code (smart_score/rules.py); the agent only writes the explanation. Shadow mode:
nothing is stored here, nothing is decided, and no amount is logged.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part
from pydantic import BaseModel, Field, model_validator

from agents.smart_score import smart_score_agent
from config import settings
from metering import MeteredRunner
from smart_score.rules import compute

logger = logging.getLogger(__name__)

APP_NAME = "loan_agents_smart_score"
AGENT_VERSION = "1"
TIMEOUT_S = 20   # the backend gives agents 30 s; the rule result is returned even without the text
MAX_MXN = 100_000_000

_sessions = InMemorySessionService()
_runner = MeteredRunner(agent=smart_score_agent, app_name=APP_NAME, session_service=_sessions)


def _authorized(x_worker_key: str = Header(default="")) -> None:
    if os.environ.get("SMART_SCORE_ENABLED") != "1" or not settings.WORKER_KEY:
        raise HTTPException(503, "smart score is disabled")
    if not hmac.compare_digest(x_worker_key.encode(), settings.WORKER_KEY.encode()):
        raise HTTPException(401, "unauthorized")


router = APIRouter(prefix="/smart-score", dependencies=[Depends(_authorized)])

_Amount = Field(default=None, ge=0, le=MAX_MXN)


class CapacityRequest(BaseModel):
    # The published policy version the backend read capRatio from (PRD AC9: no version, no result).
    policyVersion: str = Field(min_length=1, max_length=64)
    capRatio: float = Field(gt=0, le=1)
    verifiedMonthlyIncome: float | None = _Amount
    declaredMonthlyIncome: float | None = _Amount
    essentialExpensesMonthly: float | None = _Amount
    debtPaymentsMonthly: float | None = _Amount
    requestedInstallment: float | None = Field(default=None, gt=0, le=MAX_MXN)
    loanFrequency: Literal["WEEKLY", "FOURTEEN_DAYS", "SEMI_MONTHLY", "MONTHLY"] | None = None
    explain: bool = True

    @model_validator(mode="after")
    def _installment_needs_frequency(self):
        if self.requestedInstallment is not None and self.loanFrequency is None:
            raise ValueError("requestedInstallment needs loanFrequency")
        return self


class Factor(BaseModel):
    code: str
    result: str          # PASS | REVIEW | FAIL | UNKNOWN
    rule: str
    value: float | None
    reason: str


class Capacity(BaseModel):
    availableMonthly: float
    maxInstallmentMonthly: float
    maxInstallmentByFrequency: dict[str, float]


class CapacityResponse(BaseModel):
    level: str           # OK | REVIEW | INCOMPLETE | STRONG_ALERT | HIGH_ALERT — information, never a decision
    factors: list[Factor]
    capacity: Capacity | None
    explanation: str     # "" when not asked for or the agent failed: the rule result stands alone
    mode: str = "SHADOW"
    policyVersion: str
    rulesVersion: str
    inputsHash: str
    agentName: str = smart_score_agent.name
    agentVersion: str = AGENT_VERSION


async def explain(result: dict, req: CapacityRequest) -> str:
    """Best effort: a failure or timeout leaves the explanation empty."""
    user_id = f"smart-score-{uuid.uuid4()}"
    session = await _sessions.create_session(app_name=APP_NAME, user_id=user_id)
    payload = {"level": result["level"], "capacity": result["capacity"],
               "factors": [{k: f[k] for k in ("code", "result", "value", "reason")} for f in result["factors"]],
               "loanFrequency": req.loanFrequency, "requestedInstallment": req.requestedInstallment}
    message = Content(role="user", parts=[Part(text=json.dumps(payload, ensure_ascii=False))])
    text = ""

    async def run() -> None:
        nonlocal text   # this turn's final event, never session state
        async for event in _runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
            if event.is_final_response() and event.content and event.content.parts:
                text = "".join(p.text or "" for p in event.content.parts if not p.thought).strip()

    try:
        await asyncio.wait_for(run(), TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — type only: no amounts in logs
        logger.warning("[smart-score] explanation failed: %s", type(e).__name__)
        text = ""
    finally:
        await _sessions.delete_session(app_name=APP_NAME, user_id=user_id, session_id=session.id)
    return text


@router.post("/capacity", response_model=CapacityResponse)
async def capacity(req: CapacityRequest) -> CapacityResponse:
    inputs = req.model_dump(exclude={"explain"})
    result = compute(inputs)
    text = await explain(result, req) if req.explain else ""
    return CapacityResponse(**result, explanation=text, policyVersion=req.policyVersion)
