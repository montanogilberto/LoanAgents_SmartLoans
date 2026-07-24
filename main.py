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

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents import orchestrator_agent, contract_agent, id_document_agent, face_validation_agent
from config.settings import PORT

app = FastAPI(title="LoanAgents SmartLoans")

# --------------------------------------------------
# CORS configuration
# --------------------------------------------------
# /negotiate and /contract are called server-to-server by smartloans_backend,
# which needs no CORS. /extract-id-fields is different: the Ionic app calls it
# straight from the WebView, whose origin is https://localhost on Android and
# capacitor://localhost on iOS. Without this the browser blocks the preflight
# and the call fails as "TypeError: Failed to fetch" before it ever leaves the
# device. Mirrors the origin list in smartloans_backend/main.py.
origins = [
    # Local development / Capacitor WebView
    "http://localhost",
    "https://localhost",
    "http://localhost:3000",
    "http://localhost:8000",
    "http://localhost:8100",
    "http://localhost:5173",
    "capacitor://localhost",
    "ionic://localhost",

    # Azure Static Web Apps / Production
    "https://wonderful-island-0e351d910.5.azurestaticapps.net",
    "https://delightful-river-039129e0f.5.azurestaticapps.net",
    "https://mango-smoke-0323ed91e.3.azurestaticapps.net",
    "https://proud-grass-09761cb1e.1.azurestaticapps.net",
    "https://ashy-ground-041405f1e.7.azurestaticapps.net",

    # Custom domain
    "https://www.rpmtoolsmx.com",

    # Backend API (server-to-server)
    "https://smartloansbackend.azurewebsites.net",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
_face_validation_runner = Runner(agent=face_validation_agent, app_name="loan_agents_face", session_service=_session_service)


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
    # URL is preferred — smartloans_backend already uploads every capture to
    # its (public-read) blob container before this would ever run, so
    # fetching server-side avoids round-tripping the same multi-MB image as
    # base64 over the wire twice. base64 stays supported as a fallback for
    # callers that haven't uploaded yet.
    imageFrontUrl: str | None = None
    imageBackUrl: str | None = None
    imageFrontBase64: str | None = None
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


async def _fetch_image_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _resolve_image(url: str | None, image_base64: str | None) -> bytes | None:
    if url:
        return await _fetch_image_bytes(url)
    if image_base64:
        return _decode_image(image_base64)
    return None


@app.post("/extract-id-fields", response_model=ExtractIdResponse)
async def extract_id_fields(req: ExtractIdRequest) -> ExtractIdResponse:
    """Reads a Mexican INE photo (front, optionally back) and extracts KYC
    fields via Gemini vision. smartloans_backend's Azure Document
    Intelligence path reliably fails on the front's watermark-obscured
    fields (Domicilio, CURP, Clave de Elector) — this is the alternative
    for exactly those fields, and is instructed to return an empty string
    rather than guess whenever a field isn't actually legible."""
    front_bytes = await _resolve_image(req.imageFrontUrl, req.imageFrontBase64)
    if not front_bytes:
        raise HTTPException(status_code=400, detail="imageFrontUrl or imageFrontBase64 is required")
    back_bytes = await _resolve_image(req.imageBackUrl, req.imageBackBase64)

    user_id = f"idextract-{uuid.uuid4()}"
    session = await _session_service.create_session(
        app_name="loan_agents_id",
        user_id=user_id,
        session_id=str(uuid.uuid4()),
        state={"id_extraction_result": "{}"},
    )

    parts = [
        Part(text="FRONT image of the INE is attached below."),
        Part.from_bytes(data=front_bytes, mime_type="image/jpeg"),
    ]
    if back_bytes:
        parts.append(Part(text="BACK image of the INE is attached below."))
        parts.append(Part.from_bytes(data=back_bytes, mime_type="image/jpeg"))

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


class FaceAsset(BaseModel):
    """One captured asset. URL is preferred for the same reason as
    ExtractIdRequest — smartloans_backend has already uploaded every capture to
    the (public-read) blob container, so fetching server-side avoids
    round-tripping multi-MB media as base64."""
    url: str | None = None
    base64: str | None = None


class ValidateFaceRequest(BaseModel):
    # The five head positions captured during the liveness challenge. 'front'
    # is required; a session missing it cannot be judged at all.
    front: FaceAsset | None = None
    up: FaceAsset | None = None
    down: FaceAsset | None = None
    left: FaceAsset | None = None
    right: FaceAsset | None = None
    # Presence video and the INE portrait side. Both optional — the agent
    # degrades its verdict rather than erroring when they are absent.
    video: FaceAsset | None = None
    ineFront: FaceAsset | None = None


class FaceValidationCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class ValidateFaceResponse(BaseModel):
    isValid: bool
    confidence: float = 0.0
    checks: list[FaceValidationCheck] = []
    failureReasons: list[str] = []
    # Which assets actually reached the model, so a caller can tell a genuine
    # "failed validation" from "we only sent it two of the five poses".
    assetsEvaluated: list[str] = []


async def _resolve_asset(asset: FaceAsset | None) -> bytes | None:
    if asset is None:
        return None
    return await _resolve_image(asset.url, asset.base64)


@app.post("/validate-face", response_model=ValidateFaceResponse)
async def validate_face(req: ValidateFaceRequest) -> ValidateFaceResponse:
    """Reviews a whole onboarding evidence set at once: the five liveness head
    positions, the presence video, and the INE portrait.

    This is deliberately a second opinion, not a replacement for the on-device
    face-api.js check. That one compares two descriptors and cannot tell a live
    person from a phone held up to the camera, nor notice that all five "poses"
    are the same frame. Sending the whole set to a vision model catches exactly
    those cross-asset inconsistencies.
    """
    poses = {
        "front": req.front,
        "up": req.up,
        "down": req.down,
        "left": req.left,
        "right": req.right,
    }

    parts = []
    evaluated: list[str] = []

    for pose, asset in poses.items():
        data = await _resolve_asset(asset)
        if not data:
            continue
        parts.append(Part(text=f"{pose.upper()} face photo (labelled head position: {pose})."))
        parts.append(Part.from_bytes(data=data, mime_type="image/jpeg"))
        evaluated.append(pose)

    if "front" not in evaluated:
        raise HTTPException(status_code=400, detail="front face photo is required")

    ine_bytes = await _resolve_asset(req.ineFront)
    if ine_bytes:
        parts.append(Part(text="INE portrait side (the government ID to match against)."))
        parts.append(Part.from_bytes(data=ine_bytes, mime_type="image/jpeg"))
        evaluated.append("ineFront")

    video_bytes = await _resolve_asset(req.video)
    if video_bytes:
        parts.append(Part(text="PRESENCE VIDEO recorded during the same session."))
        parts.append(Part.from_bytes(data=video_bytes, mime_type="video/mp4"))
        evaluated.append("video")

    user_id = f"facevalidation-{uuid.uuid4()}"
    session = await _session_service.create_session(
        app_name="loan_agents_face",
        user_id=user_id,
        session_id=str(uuid.uuid4()),
        state={"face_validation_result": "{}"},
    )

    message = Content(role="user", parts=parts)
    async for event in _face_validation_runner.run_async(
        user_id=user_id, session_id=session.id, new_message=message
    ):
        if event.is_final_response() and event.content:
            pass  # final text is read from session state via output_key

    updated = await _session_service.get_session(
        app_name="loan_agents_face", user_id=user_id, session_id=session.id
    )
    raw = updated.state.get("face_validation_result", "{}")
    try:
        result = json.loads(_strip_json_fences(raw)) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        result = {}

    # Fail closed. An unparseable or empty model response must not read as a
    # passed identity check.
    checks = [
        FaceValidationCheck(
            name=c.get("name", ""),
            passed=bool(c.get("passed", False)),
            detail=c.get("detail", ""),
        )
        for c in result.get("checks", [])
        if isinstance(c, dict)
    ]
    if not result:
        return ValidateFaceResponse(
            isValid=False,
            confidence=0.0,
            checks=checks,
            failureReasons=["Validation agent returned no usable result."],
            assetsEvaluated=evaluated,
        )

    return ValidateFaceResponse(
        isValid=bool(result.get("isValid", False)),
        confidence=float(result.get("confidence", 0.0) or 0.0),
        checks=checks,
        failureReasons=result.get("failureReasons", []),
        assetsEvaluated=evaluated,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
