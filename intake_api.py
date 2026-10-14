"""
Business Intake endpoints — the "Ask the Factory" chat of the GMO Ecosystem
website (www.gmoecosystem.com). Included by main.py.

    POST /intake/analyze
      { "text": "...", "url": "https://their-business.com", "language": "es", "visitorId": "v_…" }
        (or "audioBase64" + "audioMimeType" for an audio note)
      -> { "status": "ok", "need", "evidence", "analysis", "website", "cached", "usage", "allowance" }
       | { "status": "needs_clarification", "need", "question", "cached", "usage" }

Read-only: the agent (agents/business_intake/) analyzes how a business can
operate inside GMO; it never writes anything. A text request costs one model
call; a repeated one is answered from the cache with none (and doesn't count
against the rate limit or the visitor's token allowance).

This endpoint is public (the website calls it from the browser) and spends
Gemini tokens, so it enforces input limits, a per-IP hourly limit, the
visitor's AI token allowance (site_commercial/) and a timeout. Every failure
is returned as an HTTPException, so it passes through the CORS middleware —
a bare 500 has no CORS headers and the browser would report it as "blocked
by CORS policy", hiding the real error.

Env:
    INTAKE_RATE_LIMIT_PER_HOUR   requests per client IP per hour (default 20)
    FACTORY_INTAKE_MODEL         model for the intake agents (default gemini-2.5-flash)
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agents.business_intake.runner import IntakeError, analyze_business_need, lookup_cached
from site_commercial import store as site_store
from site_commercial.routes import allowance, client_ip

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2000
MAX_AUDIO_BYTES = 4_000_000          # ~2 minutes of 16 kHz mono WAV
TIMEOUT_SECONDS = 90
RATE_LIMIT_PER_HOUR = int(os.getenv("INTAKE_RATE_LIMIT_PER_HOUR", "20"))
ALLOWED_AUDIO_TYPES = {
    "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/ogg", "audio/webm",
    "audio/mp4", "audio/aac", "audio/flac", "audio/x-m4a",
}

router = APIRouter()
_calls: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    window = _calls[ip]
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many analyses from this address. Try again later.")
    window.append(now)


def model_configured() -> bool:
    """True when the agents can call their model (Gemini API key, or Vertex AI project)."""
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                or (os.getenv("GCP_PROJECT") and os.getenv("GCP_LOCATION")))


class AnalyzeRequest(BaseModel):
    text: str = Field(default="", max_length=MAX_TEXT_CHARS)
    url: str = Field(default="", max_length=500, description="The business owner's website (optional).")
    visitorId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$",
                                  description="Anonymous browser id: counts this visitor's AI tokens.")
    audioBase64: str | None = None
    audioMimeType: str | None = None
    language: Literal["en", "es"] = "en"


@router.post("/intake/analyze")
async def analyze(req: AnalyzeRequest, request: Request) -> dict:
    audio: bytes | None = None
    mime = ""
    if req.audioBase64:
        mime = (req.audioMimeType or "").split(";")[0].strip().lower()
        if mime not in ALLOWED_AUDIO_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported audio type '{mime}'.")
        if len(req.audioBase64) > MAX_AUDIO_BYTES * 4 // 3 + 4:
            raise HTTPException(status_code=413, detail="Audio note is too long (about 2 minutes maximum).")
        try:
            audio = base64.b64decode(req.audioBase64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="audioBase64 is not valid base64.")
    if not req.text.strip() and not audio:
        raise HTTPException(status_code=400, detail="Write your need or record an audio note.")
    url = req.url.strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"

    if not audio and (cached := lookup_cached(req.text, url, req.language)):
        # No model call: doesn't count against the rate limit or the token allowance.
        return {**cached, **({"allowance": allowance(req.visitorId)} if req.visitorId else {})}

    if not model_configured():
        logger.error("business intake: no model key configured (set GEMINI_API_KEY)")
        raise HTTPException(status_code=503, detail={"code": "not_configured", "message": "The Factory agent is not configured yet."})

    if req.visitorId and allowance(req.visitorId)["remaining"] <= 0:
        raise HTTPException(status_code=402, detail={"code": "token_limit", "allowance": allowance(req.visitorId)})

    _check_rate_limit(client_ip(request))
    try:
        result = await asyncio.wait_for(
            analyze_business_need(text=req.text, audio=audio, audio_mime_type=mime, language=req.language, url=url),
            timeout=TIMEOUT_SECONDS,
        )
        if req.visitorId:
            site_store.add_tokens(req.visitorId, result.get("usage", {}).get("tokens", 0))
            result["allowance"] = allowance(req.visitorId)
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="The analysis took too long. Please try again.")
    except IntakeError as exc:
        logger.warning("business intake failed: %s", exc)
        raise HTTPException(status_code=502, detail="The Factory could not analyze this request. Please try again.")
    except HTTPException:
        raise
    except Exception:
        logger.exception("business intake crashed")
        raise HTTPException(status_code=502, detail="The Factory could not analyze this request. Please try again.")
