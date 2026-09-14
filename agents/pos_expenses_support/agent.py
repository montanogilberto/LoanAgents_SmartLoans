"""POS Expenses Support Agent definition.

Distinct from the existing expense_agent (advisory categorization/anomaly
flagging for the /expenses/categorize endpoint, called from the expense
CREATE flow). This agent answers a cashier's chat QUESTIONS about recorded
expenses — a different role, same "never writes" boundary."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.pos_expenses_support.prompt import INSTRUCTION
from tools.backend_api import get_expense_total, get_recent_expenses

pos_expenses_support_agent = Agent(
    name="pos_expenses_support_agent",
    description=(
        "Chat assistant for POS cashiers/admins asking about recorded expenses — "
        "explains real expense totals and recent entries, never invents a number, "
        "and never creates or edits an expense record itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_expense_total),
        FunctionTool(func=get_recent_expenses),
    ],
    output_key="pos_expenses_support_reply",
)
