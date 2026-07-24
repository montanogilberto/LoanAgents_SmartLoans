"""Face Validation Agent definition."""
from google.adk.agents import Agent

from agents.face_validation.prompt import INSTRUCTION

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
)
