from agents.risk import risk_agent
from agents.recommendation import recommendation_agent
from agents.borrower import borrower_agent
from agents.lender import lender_agent
from agents.negotiation import negotiation_agent
from agents.contract import contract_agent
from agents.orchestrator import orchestrator_agent
from agents.analysis import analysis_agent
from agents.id_document import id_document_agent
from agents.face_validation import face_validation_agent
from agents.evidence_validation import evidence_validation_agent
from agents.cash_register import cash_register_agent
from agents.expense import expense_agent
from agents.client_followup import client_followup_agent
from agents.order_triage import order_triage_agent
from agents.pos_clients_support import pos_clients_support_agent
from agents.pos_income_support import pos_income_support_agent
from agents.pos_expenses_support import pos_expenses_support_agent
from agents.pos_accounting_support import pos_accounting_support_agent

root_agent = orchestrator_agent

__all__ = [
    "risk_agent",
    "recommendation_agent",
    "borrower_agent",
    "lender_agent",
    "negotiation_agent",
    "contract_agent",
    "orchestrator_agent",
    "analysis_agent",
    "id_document_agent",
    "face_validation_agent",
    "evidence_validation_agent",
    "cash_register_agent",
    "expense_agent",
    "client_followup_agent",
    "order_triage_agent",
    "pos_clients_support_agent",
    "pos_income_support_agent",
    "pos_expenses_support_agent",
    "pos_accounting_support_agent",
    "root_agent",
]
