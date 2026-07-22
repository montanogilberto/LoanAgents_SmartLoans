"""Borrower Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.borrower.prompt import INSTRUCTION
from tools.backend_api import get_conversation

borrower_agent = Agent(
    name="borrower_agent",
    description=(
        "Articulates the borrower's ideal negotiating position (lower rate, "
        "longer term, faster approval), grounded in the real conversation "
        "terms and the Recommendation Agent's suggestion."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=get_conversation)],
    output_key="borrower_position",
)
