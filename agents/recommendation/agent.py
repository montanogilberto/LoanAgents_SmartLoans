"""Recommendation Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.recommendation.prompt import INSTRUCTION
from tools.backend_api import get_client_loans, get_client_dashboard

recommendation_agent = Agent(
    name="recommendation_agent",
    description=(
        "Suggests an alternative amount/rate/term combination with a higher "
        "approval probability, grounded in the borrower's real loans, "
        "dashboard, and the Risk Agent's prior assessment."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=get_client_loans), FunctionTool(func=get_client_dashboard)],
    output_key="recommendation",
)
