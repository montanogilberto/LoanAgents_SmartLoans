"""Structured output schema for the transfer evidence validation agent.

Passed to the agent as `output_schema`, which makes Gemini emit controlled
JSON conforming to this model instead of free text we have to hope is valid.
Mirrors agents/face_validation/schema.py's approach: enums for statuses, no
optional/default fields, and a literal "cannot assess" convention for any
extracted field the receipt doesn't make legible — never a guess.
"""
from enum import Enum

from pydantic import BaseModel


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    # The receipt doesn't show this clearly enough to judge (crop, glare,
    # low resolution) — distinct from FAIL, which means it WAS legible and
    # didn't match.
    CANNOT_ASSESS = "CANNOT_ASSESS"


class RecommendedAction(str, Enum):
    APPROVE = "APPROVE"
    REVIEW_MANUALLY = "REVIEW_MANUALLY"
    REJECT = "REJECT"


class EvidenceCheck(BaseModel):
    name: str
    status: CheckStatus
    detail: str


class TransferEvidenceResult(BaseModel):
    isValid: bool
    confidence: float                 # confidence in the ASSESSMENT, 0.0-1.0
    recommendedAction: RecommendedAction
    overallAssessment: str
    # One check per compared field: amount, transferDate, bankFrom,
    # beneficiaryName, claveRastreo (when present), plus "documentAuthenticity"
    # for whether this even looks like a real bank transfer receipt.
    checks: list[EvidenceCheck]
    # What the receipt actually shows, as read by the model — "cannot assess"
    # when illegible. Kept as strings (not float/date) since OCR'd values may
    # be partially unreadable and a schema-enforced float/date can't hold that.
    extractedAmount: str
    extractedTransferDate: str
    extractedBankFrom: str
    extractedBeneficiaryName: str
    extractedClaveRastreo: str
    mismatches: list[str]
    failureReasons: list[str]
