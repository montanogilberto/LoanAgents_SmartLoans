"""
SmartLoans AI Loan Agents — API entry point.

smartloans_backend (modules/loanChat.py) calls POST /negotiate whenever a
borrower or lender sends a message in a conversation with the AI agent
counterpart. This service is otherwise fully independent from the backend —
it only talks to it over HTTP (see tools/backend_api.py).

Run:
    uvicorn main:app --host 0.0.0.0 --port 8080
"""
import json
import uuid

from fastapi import FastAPI
from pydantic import BaseModel

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents import root_agent
from config.settings import PORT

app = FastAPI(title="LoanAgents SmartLoans")

_session_service = InMemorySessionService()
_runner = Runner(agent=root_agent, app_name="loan_agents", session_service=_session_service)


class NegotiateRequest(BaseModel):
    conversationId: int
    borrowerId: int
    companyId: int
    message: str
    speakerRole: str = "borrower"


class NegotiateResponse(BaseModel):
    reply: str


@app.post("/negotiate", response_model=NegotiateResponse)
async def negotiate(req: NegotiateRequest) -> NegotiateResponse:
    user_id = f"conv-{req.conversationId}"
    session = await _session_service.create_session(
        app_name="loan_agents",
        user_id=user_id,
        session_id=str(uuid.uuid4()),
        state={"negotiation_reply": ""},
    )

    context = {
        "conversationId": req.conversationId,
        "borrowerId": req.borrowerId,
        "companyId": req.companyId,
        "speakerRole": req.speakerRole,
    }
    message = Content(
        role="user",
        parts=[Part(text=f"{req.message}\n\nContext: {json.dumps(context)}")],
    )

    async for event in _runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
        if event.is_final_response() and event.content:
            pass  # final text is read from session state via output_key below

    updated = await _session_service.get_session(app_name="loan_agents", user_id=user_id, session_id=session.id)
    reply = updated.state.get("negotiation_reply", "").strip()
    if not reply:
        reply = "No pude generar una respuesta en este momento. Intenta de nuevo."
    return NegotiateResponse(reply=reply)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
