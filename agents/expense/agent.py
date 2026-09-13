"""Expense Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.expense.prompt import INSTRUCTION
from tools.backend_api import get_recent_expenses

expense_agent = Agent(
    name="expense_agent",
    description=(
        "Suggests a category for a new expense and flags likely duplicates or "
        "unusually high amounts against recent history — advisory only, never "
        "creates or edits an expense itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=get_recent_expenses)],
    output_key="expense_categorization",
)
