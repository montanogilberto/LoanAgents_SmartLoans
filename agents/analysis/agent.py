"""
Analysis Agent — Risk -> Recommendation only, no Borrower/Lender/Negotiation
synthesis. Used by POST /analyze-proposal (main.py) to give a lender a Smart
Score + suggested terms for one specific proposal BEFORE they accept it, as
opposed to /negotiate's full pipeline which produces a chat reply.
"""
from google.adk.agents import SequentialAgent

from agents.risk import build_risk_agent
from agents.recommendation import build_recommendation_agent

analysis_agent = SequentialAgent(
    name="analysis_agent",
    description="Proposal analysis: Risk -> Recommendation, structured output only (no chat synthesis)",
    sub_agents=[
        # Own instances — orchestrator_agent already owns the module-level
        # risk_agent/recommendation_agent, and an ADK Agent can only have one parent.
        build_risk_agent(),
        build_recommendation_agent(),
    ],
)
