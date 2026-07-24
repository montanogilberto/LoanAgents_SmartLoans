"""Structured output schema for the face validation agent.

Passed to the agent as `output_schema`, which makes Gemini emit controlled
JSON conforming to this model instead of free text we have to hope is valid.

Gemini's controlled generation has constraints, so this is written to fit them:
enums (not free strings) for statuses, a defined nested model for observed
features (no free-form dict), and no optional/default fields (the model fills
every field).
"""
from enum import Enum

from pydantic import BaseModel


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    # Distinct from FAIL: the evidence could not be judged at all (blur,
    # occlusion, missing image), rather than judged and found wrong. Matters
    # for whether a human should re-capture vs. reject.
    CANNOT_ASSESS = "CANNOT_ASSESS"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RecommendedAction(str, Enum):
    APPROVE = "APPROVE"
    REVIEW_MANUALLY = "REVIEW_MANUALLY"
    REJECT = "REJECT"


class FaceCheck(BaseModel):
    name: str
    status: CheckStatus
    detail: str


class ObservedFeatures(BaseModel):
    # A defined model, not a free dict — Gemini controlled generation needs
    # named properties. Age is intentionally absent: estimated age is
    # unreliable and not needed for verification. Any field that can't be seen
    # must be the literal string "cannot assess".
    hair: str
    facialHair: str
    glasses: str
    visibleMoles: str
    headwear: str


class FaceValidationResult(BaseModel):
    isValid: bool
    confidence: float               # confidence in the ASSESSMENT, 0.0–1.0
    riskLevel: RiskLevel
    recommendedAction: RecommendedAction
    overallAssessment: str
    checks: list[FaceCheck]
    observedFeatures: ObservedFeatures
    presentationAttackDetected: bool
    # Three independent scores — kept separate on purpose so downstream review
    # can tell "not the same person across poses" from "doesn't match the ID"
    # from "not a live human".
    identityConsistencyScore: float   # same person across the pose images
    ineSimilarityScore: float         # that person vs. the INE portrait
    livenessScore: float              # real live human, not a replay/photo
    failureReasons: list[str]
