from agents.risk import risk_agent
from agents.recommendation import recommendation_agent
from agents.borrower import borrower_agent
from agents.lender import lender_agent
from agents.negotiation import negotiation_agent
from agents.contract import contract_agent
from agents.orchestrator import orchestrator_agent
from agents.id_document import id_document_agent

root_agent = orchestrator_agent

__all__ = [
    "risk_agent",
    "recommendation_agent",
    "borrower_agent",
    "lender_agent",
    "negotiation_agent",
    "contract_agent",
    "orchestrator_agent",
    "id_document_agent",
    "root_agent",
]
