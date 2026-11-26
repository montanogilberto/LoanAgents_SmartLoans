"""
Comprobantes vs INE (included by main.py). Server-to-server only.

    POST /proof-documents/address   utility bill  → holder + address vs INE
    POST /proof-documents/payroll   payslip       → worker + CURP vs INE

Off unless PROOF_DOCUMENTS_ENABLED=1, and then only with X-Worker-Key: the INE
data must come from smartloans_backend, never from the applicant's device.
Proposes only; nothing is stored here and no document content is logged.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part
from pydantic import BaseModel, Field

from agents.payroll_receipt import payroll_receipt_agent
from agents.proof_of_address import proof_of_address_agent
from config import settings
from metering import MeteredRunner
from proof_documents.review import review_address, review_payroll

logger = logging.getLogger(__name__)

APP_NAME = "loan_agents_proof_documents"
AGENT_VERSION = "1"
MAX_BYTES = 10 * 1024 * 1024
TIMEOUT_S = 27   # the backend gives agents 30 s

_sessions = InMemorySessionService()
_address_runner = MeteredRunner(agent=proof_of_address_agent, app_name=APP_NAME, session_service=_sessions)
_payroll_runner = MeteredRunner(agent=payroll_receipt_agent, app_name=APP_NAME, session_service=_sessions)


def _authorized(x_worker_key: str = Header(default="")) -> None:
    if os.environ.get("PROOF_DOCUMENTS_ENABLED") != "1" or not settings.WORKER_KEY:
        raise HTTPException(503, "proof documents is disabled")
    if not hmac.compare_digest(x_worker_key.encode(), settings.WORKER_KEY.encode()):
        raise HTTPException(401, "unauthorized")


router = APIRouter(prefix="/proof-documents", dependencies=[Depends(_authorized)])


class _Ine(BaseModel):
    name: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=400)
    curp: str = Field(default="", max_length=30)


class ProofRequest(BaseModel):
    documentBase64: str = Field(min_length=1, max_length=MAX_BYTES * 4 // 3 + 200)   # raw or data URL: image or PDF
    ine: _Ine
    maxAgeDays: int = Field(default=90, ge=1, le=365)


class ProofResponse(BaseModel):
    verdict: str                      # MATCH | REVIEW | MISMATCH | WRONG_DOCUMENT | UNREADABLE
    checks: dict[str, str]            # MATCH | PARTIAL | MISMATCH | MISSING
    addressParts: dict[str, str] = {}
    monthlyNetEstimate: float | None = None   # payroll only; a proposal, the SP computes the stored one
    reasons: list[str]
    confidence: float
    fields: dict
    agentName: str
    agentVersion: str = AGENT_VERSION
    contentHash: str


def detect_mime(data: bytes) -> str | None:
    """From the bytes, never from the declared type."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1"):
        return "image/heic"
    return None


def _decode(document: str) -> tuple[bytes, str]:
    raw = document.split(",", 1)[-1] if document.startswith("data:") else document
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "documentBase64 is not valid base64")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "document is larger than 10 MB")
    mime = detect_mime(data)
    if not mime:
        raise HTTPException(415, "document must be a PDF, JPEG, PNG, WEBP or HEIC")
    return data, mime


async def _read(runner: MeteredRunner, data: bytes, mime: str) -> dict:
    user_id = f"proof-{uuid.uuid4()}"
    session = await _sessions.create_session(app_name=APP_NAME, user_id=user_id)
    message = Content(role="user", parts=[
        Part.from_bytes(data=data, mime_type=mime), Part(text="Lee este documento."),
    ])
    text = ""

    async def run() -> None:
        nonlocal text
        async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
            if event.is_final_response() and event.content and event.content.parts:
                text = "".join(p.text or "" for p in event.content.parts if not p.thought)

    try:
        await asyncio.wait_for(run(), TIMEOUT_S)
    except TimeoutError:
        raise HTTPException(504, "document reading timed out")
    except Exception as e:  # noqa: BLE001 — type only: the message could carry document content
        logger.warning("[proof-documents] reading failed: %s", type(e).__name__)
        raise HTTPException(502, "document reading failed")
    finally:   # the session holds the whole document
        await _sessions.delete_session(app_name=APP_NAME, user_id=user_id, session_id=session.id)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return result if isinstance(result, dict) else {}


async def _handle(req: ProofRequest, runner: MeteredRunner, review, agent_name: str) -> ProofResponse:
    data, mime = _decode(req.documentBase64)
    doc = await _read(runner, data, mime)
    today = datetime.now(timezone(timedelta(hours=-7))).date()   # Hermosillo
    result = review(doc, req.ine.model_dump(), today, req.maxAgeDays)
    return ProofResponse(**result, agentName=agent_name, contentHash=hashlib.sha256(data).hexdigest())


@router.post("/address", response_model=ProofResponse)
async def check_address(req: ProofRequest) -> ProofResponse:
    return await _handle(req, _address_runner, review_address, proof_of_address_agent.name)


@router.post("/payroll", response_model=ProofResponse)
async def check_payroll(req: ProofRequest) -> ProofResponse:
    return await _handle(req, _payroll_runner, review_payroll, payroll_receipt_agent.name)
