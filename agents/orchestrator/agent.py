"""
Orchestrator Agent — coordinates the full negotiation pipeline:

    Risk Agent -> Recommendation Agent -> Borrower Agent -> Lender Agent
    -> Negotiation Agent (final synthesis, this is what the borrower/lender
       actually see in the chat)

Contract Agent runs separately (see main.py's /contract endpoint) — it's a
terminal, one-time action once a conversation reaches 'accepted' status, not
something that should run on every chat message.
"""
from google.adk.agents import SequentialAgent

from agents.risk import risk_agent
from agents.recommendation import recommendation_agent
from agents.borrower import borrower_agent
from agents.lender import lender_agent
from agents.negotiation import negotiation_agent

orchestrator_agent = SequentialAgent(
    name="orchestrator_agent",
    description="Full SmartLoans negotiation pipeline: Risk -> Recommendation -> Borrower -> Lender -> Negotiation",
    sub_agents=[
        risk_agent,
        recommendation_agent,
        borrower_agent,
        lender_agent,
        negotiation_agent,
    ],
)
