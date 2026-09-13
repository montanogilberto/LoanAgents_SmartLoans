"""POS Clients Support Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.pos_clients_support.prompt import INSTRUCTION
from tools.backend_api import get_one_client

pos_clients_support_agent = Agent(
    name="pos_clients_support_agent",
    description=(
        "Chat assistant for POS cashiers/admins using the 'Clientes' new-"
        "client registration wizard — explains each step and troubleshoots, "
        "never registers or edits a client itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_one_client),
    ],
    output_key="pos_clients_support_reply",
)
