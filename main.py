"""
SmartLoans AI Loan Agents — API entry point.

smartloans_backend (modules/loanChat.py) calls POST /negotiate whenever a
borrower or lender sends a message in a conversation with the AI agent
counterpart, and POST /contract once a conversation reaches 'accepted'
status. This service is otherwise fully independent from the backend — it
only talks to it over HTTP (see tools/backend_api.py).

Run:
    uvicorn main:app --host 0.0.0.0 --port 8080
"""
import base64
import json
import uuid

from fastapi import FastAPI
from pydantic import BaseModel

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents import orchestrator_agent, contract_agent, id_document_agent
from config.settings import PORT

app = FastAPI(title="LoanAgents SmartLoans")


def _strip_json_fences(text: str) -> str:
    """Gemini occasionally wraps JSON output in ```json ... ``` fences
    despite being told not to — strip them before json.loads()."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[: -3]
    return stripped.strip()

_session_service = InMemorySessionService()
_negotiate_runner = Runner(agent=orchestrator_agent, app_name="loan_agents", session_service=_session_service)
_contract_runner = Runner(agent=contract_agent, app_name="loan_agents_contract", session_service=_session_service)
_id_extraction_runner = Runner(agent=id_document_agent, app_name="loan_agents_id", session_service=_session_service)


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
    """Runs the full pipeline: Risk -> Recommendation -> Borrower -> Lender
    -> Negotiation. Only the Negotiation Agent's synthesis is returned —
    the others are intermediate reasoning steps."""
    user_id = f"conv-{req.conversationId}"
    session = await _session_service.create_session(
        app_name="loan_agents",
        user_id=user_id,
        session_id=str(uuid.uuid4()),
        # Seeded so a downstream agent never KeyErrors reading a step an
        # earlier agent failed to write.
        state={
            "risk_assessment": "{}",
            "recommendation": "{}",
            "borrower_position": "{}",
            "lender_position": "{}",
            "negotiation_reply": "",
        },
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

    async for event in _negotiate_runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
        if event.is_final_response() and event.content:
            pass  # final text is read from session state via output_key below

    updated = await _session_service.get_session(app_name="loan_agents", user_id=user_id, session_id=session.id)
    reply = updated.state.get("negotiation_reply", "").strip()
    if not reply:
        reply = "No pude generar una respuesta en este momento. Intenta de nuevo."
    return NegotiateResponse(reply=reply)


class ContractRequest(BaseModel):
    conversationId: int
    loanId: int
    companyId: int
    borrowerClientId: int
    lenderClientId: int


class ContractResponse(BaseModel):
    status: str
    contractId: int | None = None
    message: str


@app.post("/contract", response_model=ContractResponse)
async def contract(req: ContractRequest) -> ContractResponse:
    """Runs the Contract Agent once a conversation has reached 'accepted'
    status — a one-time terminal action, not something that runs per message."""
    user_id = f"contract-{req.conversationId}"
    session = await _session_service.create_session(
        app_name="loan_agents_contract",
        user_id=user_id,
        session_id=str(uuid.uuid4()),
        state={"contract_result": "{}"},
    )

    context = {
        "conversationId": req.conversationId,
        "loanId": req.loanId,
        "companyId": req.companyId,
        "borrowerClientId": req.borrowerClientId,
        "lenderClientId": req.lenderClientId,
    }
    message = Content(role="user", parts=[Part(text=json.dumps(context))])

    async for event in _contract_runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
        if event.is_final_response() and event.content:
            pass

    updated = await _session_service.get_session(app_name="loan_agents_contract", user_id=user_id, session_id=session.id)
    raw = updated.state.get("contract_result", "{}")
    try:
        result = json.loads(_strip_json_fences(raw)) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        result = {}

    return ContractResponse(
        status=result.get("status", "error"),
        contractId=result.get("contractId"),
        message=result.get("message", "No se pudo procesar el contrato."),
    )


class ExtractIdRequest(BaseModel):
    imageFrontBase64: str
    imageBackBase64: str | None = None


class ExtractIdFields(BaseModel):
    nombre: str = ""
    domicilio: str = ""
    curp: str = ""
    claveElector: str = ""
    fechaNacimiento: str = ""


class ExtractIdResponse(BaseModel):
    fields: ExtractIdFields
    lowConfidenceFields: list[str] = []


def _decode_image(image_base64: str) -> bytes:
    data = image_base64.split(",", 1)[1] if image_base64.startswith("data:") else image_base64
    return base64.b64decode(data)


@app.post("/extract-id-fields", response_model=ExtractIdResponse)
async def extract_id_fields(req: ExtractIdRequest) -> ExtractIdResponse:
    """Reads a Mexican INE photo (front, optionally back) and extracts KYC
    fields via Gemini vision. smartloans_backend's Azure Document
    Intelligence path reliably fails on the front's watermark-obscured
    fields (Domicilio, CURP, Clave de Elector) — this is the alternative
    for exactly those fields, and is instructed to return an empty string
    rather than guess whenever a field isn't actually legible."""
    user_id = f"idextract-{uuid.uuid4()}"
    session = await _session_service.create_session(
        app_name="loan_agents_id",
        user_id=user_id,
        session_id=str(uuid.uuid4()),
        state={"id_extraction_result": "{}"},
    )

    parts = [
        Part(text="FRONT image of the INE is attached below."),
        Part.from_bytes(data=_decode_image(req.imageFrontBase64), mime_type="image/jpeg"),
    ]
    if req.imageBackBase64:
        parts.append(Part(text="BACK image of the INE is attached below."))
        parts.append(Part.from_bytes(data=_decode_image(req.imageBackBase64), mime_type="image/jpeg"))

    message = Content(role="user", parts=parts)

    async for event in _id_extraction_runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
        if event.is_final_response() and event.content:
            pass  # final text is read from session state via output_key below

    updated = await _session_service.get_session(app_name="loan_agents_id", user_id=user_id, session_id=session.id)
    raw = updated.state.get("id_extraction_result", "{}")
    try:
        result = json.loads(_strip_json_fences(raw)) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        result = {}

    fields = ExtractIdFields(
        nombre=result.get("nombre", ""),
        domicilio=result.get("domicilio", ""),
        curp=result.get("curp", ""),
        claveElector=result.get("claveElector", ""),
        fechaNacimiento=result.get("fechaNacimiento", ""),
    )
    return ExtractIdResponse(fields=fields, lowConfidenceFields=result.get("lowConfidenceFields", []))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
