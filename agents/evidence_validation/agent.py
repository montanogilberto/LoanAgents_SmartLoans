"""Transfer Evidence Validation Agent definition."""
from google.adk.agents import Agent
from google.genai import types

from agents.evidence_validation.prompt import INSTRUCTION
from agents.evidence_validation.schema import TransferEvidenceResult

evidence_validation_agent = Agent(
    name="evidence_validation_agent",
    description=(
        "Reviews a photo of a SPEI transfer receipt (comprobante) against the "
        "declared amount/date/bank/beneficiary and reports per-field matches "
        "or mismatches — advisory evidence validation, not a money-movement "
        "or loan-activation decision."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="transfer_evidence_result",
    # Force controlled JSON conforming to the schema, same as face_validation_agent.
    output_schema=TransferEvidenceResult,
    # Near-deterministic: this feeds a funding record, so the same receipt
    # must produce the same verdict rather than drifting between evaluations.
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.1,
        top_k=1,
    ),
)
