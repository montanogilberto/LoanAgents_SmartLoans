"""Order Triage Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.order_triage.prompt import INSTRUCTION
from tools.backend_api import list_open_orders

order_triage_agent = Agent(
    name="order_triage_agent",
    description=(
        "Summarizes today's orders and flags any stuck too long in a "
        "non-terminal status — read-only triage, never changes an order's "
        "status itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=list_open_orders)],
    output_key="order_triage_result",
)
