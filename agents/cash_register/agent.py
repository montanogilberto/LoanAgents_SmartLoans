"""Cash Register Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.cash_register.prompt import INSTRUCTION
from tools.backend_api import get_cash_register_daily_summary, list_cash_register_movements

cash_register_agent = Agent(
    name="cash_register_agent",
    description=(
        "Reviews a cash register's daily close-out summary and flags whether "
        "expected vs. physical cash balances — advisory only, never opens, "
        "closes, or adjusts the register itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_cash_register_daily_summary),
        FunctionTool(func=list_cash_register_movements),
    ],
    output_key="cash_register_review",
)
