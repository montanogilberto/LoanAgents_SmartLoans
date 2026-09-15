"""POS Clients Support Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.pos_clients_support.prompt import INSTRUCTION
from retrieval.graph import describe_schema
from retrieval.keyword_search import search_docs
from tools.backend_api import (
    get_one_client, list_clients, get_reward_balance, get_reward_transactions,
    resolve_income_receipt,
)
from tools.pending_actions import propose_action

pos_clients_support_agent = Agent(
    name="pos_clients_support_agent",
    description=(
        "Chat assistant for POS cashiers/admins using the 'Clientes' new-"
        "client registration wizard — explains each step, troubleshoots, "
        "lists/searches real registered clients, traces a client's reward "
        "points back through the real sale that earned them (multi-hop "
        "GMO relationship graph — see retrieval/graph.py), and can "
        "PROPOSE creating a client when the cashier's message already "
        "gives every required field (never executes the write itself — "
        "the backend does that only after explicit confirmation)."
    ),
    model="gemini-2.5-flash",
    # describe_schema() is appended live so the prompt's graph description
    # can never drift from retrieval/graph.py's actual, verified edges.
    instruction=lambda _ctx: INSTRUCTION + "\n\n## GMO relationship graph (real, verified edges)\n" + describe_schema(),
    tools=[
        FunctionTool(func=get_one_client),
        FunctionTool(func=list_clients),
        FunctionTool(func=get_reward_balance),
        FunctionTool(func=get_reward_transactions),
        FunctionTool(func=resolve_income_receipt),
        FunctionTool(func=search_docs),
        FunctionTool(func=propose_action),
    ],
    output_key="pos_clients_support_reply",
)
