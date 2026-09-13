"""Client Follow-Up Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.client_followup.prompt import INSTRUCTION
from tools.backend_api import get_client_follow_ups, get_client_loans

client_followup_agent = Agent(
    name="client_followup_agent",
    description=(
        "Reads a client's follow-up history and loan status and suggests the "
        "next follow-up action and a risk status — advisory only, never "
        "contacts the client or changes any record itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_client_follow_ups),
        FunctionTool(func=get_client_loans),
    ],
    output_key="client_followup_suggestion",
)
