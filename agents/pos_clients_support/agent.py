"""POS Clients Support Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.pos_clients_support.prompt import INSTRUCTION
from tools.backend_api import get_one_client
from tools.pending_actions import propose_action

pos_clients_support_agent = Agent(
    name="pos_clients_support_agent",
    description=(
        "Chat assistant for POS cashiers/admins using the 'Clientes' new-"
        "client registration wizard — explains each step, troubleshoots, "
        "and can PROPOSE creating a client when the cashier's message "
        "already gives every required field (never executes the write "
        "itself — the backend does that only after explicit confirmation)."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_one_client),
        FunctionTool(func=propose_action),
    ],
    output_key="pos_clients_support_reply",
)
