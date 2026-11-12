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
from agents.ticket_extraction import ticket_extraction_agent
from agents.transfer_extraction import transfer_extraction_agent
from agents.product_extraction import product_extraction_agent
from agents.product_web_lookup import product_web_lookup_agent
from agents.client_followup import client_followup_agent
from agents.order_triage import order_triage_agent
from agents.pos_clients_support import pos_clients_support_agent
from agents.pos_income_support import pos_income_support_agent
from agents.pos_expenses_support import pos_expenses_support_agent
from agents.pos_accounting_support import pos_accounting_support_agent
from agents.pos_rewards_support import pos_rewards_support_agent
from agents.whatsapp_reservations import whatsapp_reservations_agent
from agents.whatsapp_registration import whatsapp_registration_agent
from agents.whatsapp_campaign import whatsapp_campaign_agent
from agents.voice_secretary import voice_secretary_agent
from agents.whatsapp_topic import whatsapp_topic_agent
from agents.whatsapp_insights import whatsapp_insights_agent

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
    "ticket_extraction_agent",
    "product_extraction_agent",
    "product_web_lookup_agent",
    "client_followup_agent",
    "order_triage_agent",
    "pos_clients_support_agent",
    "pos_income_support_agent",
    "pos_expenses_support_agent",
    "pos_accounting_support_agent",
    "pos_rewards_support_agent",
    "whatsapp_reservations_agent",
    "whatsapp_registration_agent",
    "whatsapp_campaign_agent",
    "voice_secretary_agent",
    "whatsapp_topic_agent",
    "whatsapp_insights_agent",
    "root_agent",
]
