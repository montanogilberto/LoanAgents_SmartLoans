"""Risk Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.risk.prompt import INSTRUCTION
from tools.backend_api import get_credit_score

risk_agent = Agent(
    name="risk_agent",
    description=(
        "Reads a borrower's real credit score and recommends a risk tier and "
        "interest rate adjustment — never negotiates directly, only assesses."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=get_credit_score)],
    output_key="risk_assessment",
)
