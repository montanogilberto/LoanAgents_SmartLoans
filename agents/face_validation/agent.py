"""Face Validation Agent definition."""
from google.adk.agents import Agent
from google.genai import types

from agents.face_validation.prompt import INSTRUCTION
from agents.face_validation.schema import FaceValidationResult

face_validation_agent = Agent(
    name="face_validation_agent",
    description=(
        "Reviews a full onboarding evidence set — five head-position photos "
        "(front/up/down/left/right) from the liveness challenge, the presence "
        "video, and the INE portrait — and judges whether they show one real, "
        "present person matching the ID. Reports per-check findings rather "
        "than a bare pass/fail, and says 'cannot assess' instead of guessing "
        "when an image is unreadable."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="face_validation_result",
    # Force controlled JSON conforming to the schema instead of hoping the
    # "return only JSON" instruction holds — ADK sets response_mime_type +
    # response_schema from this and the model can no longer emit malformed or
    # fenced output.
    output_schema=FaceValidationResult,
    # Near-deterministic: this gates loan approval, so the same evidence must
    # produce the same verdict rather than drifting between evaluations.
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.1,
        top_k=1,
    ),
)
