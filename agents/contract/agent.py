"""Contract Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.contract.prompt import INSTRUCTION
from tools.backend_api import get_conversation, create_contract

contract_agent = Agent(
    name="contract_agent",
    description=(
        "Once a borrower and lender have agreed on terms, prepares the "
        "digital loan contract via smartloans_backend's digitalContracts API. "
        "Never negotiates, never invents terms."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=get_conversation), FunctionTool(func=create_contract)],
    output_key="contract_result",
)
