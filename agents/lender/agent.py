"""Lender Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.lender.prompt import INSTRUCTION
from tools.backend_api import get_conversation

lender_agent = Agent(
    name="lender_agent",
    description=(
        "Articulates the lender's ideal negotiating position (rate covers "
        "risk, protect capital, reduce default risk), grounded in the real "
        "conversation terms and the Risk Agent's assessment."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=get_conversation)],
    output_key="lender_position",
)
