"""Explains a smartScore result in es-MX. The capacity rule is code (smart_score/rules.py);
this agent has no tools, sees only that result, and never decides."""
from google.adk.agents import Agent
from google.genai import types

from agents.smart_score.prompt import INSTRUCTION

smart_score_agent = Agent(
    name="smart_score_agent",
    description="Explains a deterministic payment-capacity result to the borrower. Never computes or decides.",
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)
