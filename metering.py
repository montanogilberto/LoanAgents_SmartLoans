"""Token metering — records what each agent run really cost, per company.

Every agent call spends real Gemini tokens. This module sums the token counts
the model reports on each ADK event and, when the run ends, posts ONE usage
record to smartloans_backend (POST /company_tokens/record, guarded by the shared
worker key). The POS shows each company's remaining balance from that ledger.

Design constraints:
  * Metering must NEVER break or slow an agent reply: recording is best-effort,
    time-boxed, and every failure is only logged.
  * One choke point instead of editing ~20 endpoints: MeteredRunner wraps
    Runner.run_async, and `metering_middleware` tells it which company /
    endpoint the current request belongs to (via contextvars).
  * Requests without a top-level integer `companyId` (KYC / loan flows) are
    not billed to anyone and are skipped.
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar

import httpx
from google.adk.runners import Runner

from config.settings import SMARTLOANS_BACKEND_URL, WORKER_KEY

logger = logging.getLogger(__name__)

_company_id: ContextVar[int | None] = ContextVar("metering_company_id", default=None)
_endpoint: ContextVar[str] = ContextVar("metering_endpoint", default="")

RECORD_TIMEOUT_S = 5.0
_warned_no_key = False


def _tokens(usage, name: str) -> int:
    value = getattr(usage, name, None)
    return value if isinstance(value, int) and value > 0 else 0


class UsageMeter:
    """Accumulates the token usage reported across one agent run."""

    def __init__(self, company_id: int | None, endpoint: str, agent_name: str, model: str):
        self.company_id = company_id
        self.endpoint = endpoint
        self.agent_name = agent_name
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0
        self.thoughts_tokens = 0

    def add(self, event) -> None:
        usage = getattr(event, "usage_metadata", None)
        if usage is None:
            return
        self.input_tokens += _tokens(usage, "prompt_token_count")
        self.output_tokens += _tokens(usage, "candidates_token_count")
        self.thoughts_tokens += _tokens(usage, "thoughts_token_count")

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.thoughts_tokens

    def payload(self) -> dict:
        return {"tokens": [{
            "companyId": self.company_id,
            "agentName": self.agent_name,
            "endpointName": self.endpoint,
            "model": self.model,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "thoughtsTokens": self.thoughts_tokens,
        }]}

    async def flush(self) -> None:
        """Posts the usage to the backend. Never raises."""
        global _warned_no_key
        if self.company_id is None or self.total <= 0:
            return
        if not WORKER_KEY:
            if not _warned_no_key:
                logger.warning("metering: WORKER_KEY is not set — token usage is NOT being recorded.")
                _warned_no_key = True
            return
        try:
            async with httpx.AsyncClient(timeout=RECORD_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{SMARTLOANS_BACKEND_URL}/company_tokens/record",
                    json=self.payload(),
                    headers={"X-Worker-Key": WORKER_KEY},
                )
                resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — metering must never break the reply
            logger.warning("metering: could not record %s tokens for company %s (%s): %s",
                           self.total, self.company_id, self.endpoint, exc)


class MeteredRunner(Runner):
    """A Runner that bills each run's real token usage to the request's company."""

    async def run_async(self, *args, **kwargs):
        agent = getattr(self, "agent", None)
        model = getattr(agent, "model", "") or ""
        meter = UsageMeter(
            company_id=_company_id.get(),
            endpoint=_endpoint.get(),
            agent_name=getattr(agent, "name", "") or "",
            model=model if isinstance(model, str) else str(model),
        )
        try:
            async for event in super().run_async(*args, **kwargs):
                meter.add(event)
                yield event
        finally:
            await meter.flush()


async def metering_middleware(request, call_next):
    """Tells MeteredRunner which company and endpoint this request is for."""
    company_id = None
    if request.method == "POST":
        try:
            body = json.loads(await request.body() or b"{}")
            value = body.get("companyId") if isinstance(body, dict) else None
            company_id = value if isinstance(value, int) and not isinstance(value, bool) else None
        except (ValueError, UnicodeDecodeError):
            company_id = None
    company_token = _company_id.set(company_id)
    endpoint_token = _endpoint.set(request.url.path)
    try:
        return await call_next(request)
    finally:
        _company_id.reset(company_token)
        _endpoint.reset(endpoint_token)
