"""Negotiation Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.negotiation.prompt import INSTRUCTION
from tools.backend_api import get_conversation, get_client_loans, get_client_dashboard, get_credit_score

negotiation_agent = Agent(
    name="negotiation_agent",
    description=(
        "Helps a borrower and lender reach a loan agreement faster by reading "
        "the conversation, the borrower's real account data, and suggesting "
        "grounded, concrete negotiation moves."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_conversation),
        FunctionTool(func=get_client_loans),
        FunctionTool(func=get_client_dashboard),
        FunctionTool(func=get_credit_score),
    ],
    output_key="negotiation_reply",
)
