"""POS Accounting Support Agent definition.

Answers using the REAL ledger (journalEntries/journalEntryLines via the
trial-balance projection) — never a hand-aggregated approximation. Income
and expense inserts already auto-post there (modules/journalEntries.py in
the backend), so this reflects actually-posted transactions, not a parallel
accounting model."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.pos_accounting_support.prompt import INSTRUCTION
from retrieval.keyword_search import search_docs
from tools.backend_api import get_trial_balance

pos_accounting_support_agent = Agent(
    name="pos_accounting_support_agent",
    description=(
        "Chat assistant for POS cashiers/admins asking about the accounting "
        "result — explains the real trial balance (Balanza de Comprobación), "
        "never invents a figure, and never posts or edits a journal entry itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_trial_balance),
        FunctionTool(func=search_docs),
    ],
    output_key="pos_accounting_support_reply",
)
