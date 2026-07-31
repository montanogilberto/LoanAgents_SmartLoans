"""Support Agent definition — cuenta, contratos y legal (GUÍA).

Runs INSTEAD of the negotiation pipeline when /negotiate receives a support
request (a `topic`, or a lender speaker): support questions don't need the
Risk → Recommendation → Borrower → Lender chain, and running it would both
slow the reply and push negotiation framing into a support answer.
"""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.support.prompt import INSTRUCTION
from tools.backend_api import (
    get_client_loans,
    get_wallet_balance,
    get_wallet_movements,
    get_bank_accounts,
    get_installment_schedule,
    get_client_contracts,
    get_my_offers,
)

support_agent = Agent(
    name="support_agent",
    description=(
        "Answers a borrower's or lender's questions about their own account "
        "(wallet, movements, CLABE, loans, cuotas), their digital contracts "
        "(contrato + pagaré), and general legal orientation (GUÍA)."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_client_loans),
        FunctionTool(func=get_wallet_balance),
        FunctionTool(func=get_wallet_movements),
        FunctionTool(func=get_bank_accounts),
        FunctionTool(func=get_installment_schedule),
        FunctionTool(func=get_client_contracts),
        FunctionTool(func=get_my_offers),
    ],
    output_key="support_reply",
)
