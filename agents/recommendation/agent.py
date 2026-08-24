"""Recommendation Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.recommendation.prompt import INSTRUCTION
from tools.backend_api import get_client_loans, get_client_dashboard


def build_recommendation_agent() -> Agent:
    # A fresh instance per parent — an ADK Agent can only belong to one
    # SequentialAgent (orchestrator_agent's or analysis_agent's) at a time.
    return Agent(
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


recommendation_agent = build_recommendation_agent()
