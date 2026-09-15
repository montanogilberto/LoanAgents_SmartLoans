"""POS Expenses Support Agent definition.

Distinct from the existing expense_agent (advisory categorization/anomaly
flagging for the /expenses/categorize endpoint, called from the expense
CREATE flow). This agent answers a cashier's chat QUESTIONS about recorded
expenses — a different role, same "never writes" boundary."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.pos_expenses_support.prompt import INSTRUCTION
from retrieval.hybrid import hybrid_search
from tools.backend_api import get_expense_total, get_recent_expenses
from tools.pending_actions import propose_action

pos_expenses_support_agent = Agent(
    name="pos_expenses_support_agent",
    description=(
        "Chat assistant for POS cashiers/admins asking about recorded expenses — "
        "explains real expense totals and recent entries, and can PROPOSE "
        "registering a new general/payroll expense when the cashier's message "
        "already gives every required field (never executes the write itself — "
        "the backend does that only after explicit confirmation)."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_expense_total),
        FunctionTool(func=get_recent_expenses),
        FunctionTool(func=hybrid_search),
        FunctionTool(func=propose_action),
    ],
    output_key="pos_expenses_support_reply",
)
