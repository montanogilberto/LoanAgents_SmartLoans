"""POS Income Support Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.pos_income_support.prompt import INSTRUCTION
from tools.backend_api import get_monthly_income

pos_income_support_agent = Agent(
    name="pos_income_support_agent",
    description=(
        "Chat assistant for POS cashiers/admins asking about recorded income — "
        "explains real income figures for the current month, never invents or "
        "estimates a number, and never creates or edits an income record itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_monthly_income),
    ],
    output_key="pos_income_support_reply",
)
