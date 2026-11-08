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
        output = _tokens(usage, "candidates_token_count")
        if not output:
            # Gemini Live reports output as response_token_count, which ADK drops when it
            # copies the usage into GenerateContentResponseUsageMetadata — only the total
            # survives, so output is what the total has beyond the input and thoughts.
            output = _tokens(usage, "response_token_count") or max(0, _tokens(usage, "total_token_count")
                - _tokens(usage, "prompt_token_count") - _tokens(usage, "thoughts_token_count")
                - _tokens(usage, "tool_use_prompt_token_count"))
        self.output_tokens += output
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
        model = getattr(model, "model", model)   # a model object (e.g. LiveGemini) → its name
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

    async def run_live(self, *args, **kwargs):
        """Live (voice) sessions: one usage record per call. A WebSocket doesn't
        pass through metering_middleware, so the route sets the company with
        bind_metering() before starting the call."""
        agent = getattr(self, "agent", None)
        model = getattr(agent, "model", "") or ""
        model = getattr(model, "model", model)   # a model object (e.g. LiveGemini) → its name
        meter = UsageMeter(
            company_id=_company_id.get(),
            endpoint=_endpoint.get(),
            agent_name=getattr(agent, "name", "") or "",
            model=model if isinstance(model, str) else str(model),
        )
        try:
            async for event in super().run_live(*args, **kwargs):
                meter.add(event)
                yield event
        finally:
            await meter.flush()


def bind_metering(company_id: int | None, endpoint: str):
    """For routes the HTTP middleware doesn't see (WebSockets). Returns tokens for unbind_metering."""
    return _company_id.set(company_id), _endpoint.set(endpoint)


def unbind_metering(tokens) -> None:
    company_token, endpoint_token = tokens
    _company_id.reset(company_token)
    _endpoint.reset(endpoint_token)


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
