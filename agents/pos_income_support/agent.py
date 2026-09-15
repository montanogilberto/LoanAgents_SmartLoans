"""POS Income Support Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.pos_income_support.prompt import INSTRUCTION
from retrieval.keyword_search import search_docs
from tools.backend_api import get_monthly_income
from tools.pending_actions import propose_action

pos_income_support_agent = Agent(
    name="pos_income_support_agent",
    description=(
        "Chat assistant for POS cashiers/admins asking about recorded income — "
        "explains real income figures for the current month, and can PROPOSE "
        "registering a new income record when the cashier's message already "
        "gives every required field (never executes the write itself — the "
        "backend does that only after explicit confirmation)."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_monthly_income),
        FunctionTool(func=search_docs),
        FunctionTool(func=propose_action),
    ],
    output_key="pos_income_support_reply",
)
