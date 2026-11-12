"""WhatsApp Registration Agent — a sub-agent of whatsapp_reservations_agent.

Registers the customer writing as a client of the business they wrote to.
Only PROPOSES (REGISTER_CLIENT, full name only); smartloans_backend's
whatsappReservations.reply_to creates the client after their "sí", with the
phone and companyId of the WhatsApp channel — never values the model chose.
"""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.whatsapp_registration.prompt import INSTRUCTION
from tools.backend_api import get_my_registration
from tools.pending_actions import propose_action
from tools.safe_tool import never_raise

def make_registration_agent(parent_name: str = "whatsapp_reservations_agent") -> Agent:
    """A fresh instance: an ADK agent can have only one parent, and both the
    company agent and the campaign agent hand registration to it."""
    return Agent(
        name="whatsapp_registration_agent",
        description=(
            "Registers the WhatsApp customer as a new client of this business: asks "
            "only their full name (the phone is the number they write from) and "
            "PROPOSES REGISTER_CLIENT; the backend creates it after their \"sí\". "
            "Use when the customer wants to register / darse de alta / crear su cuenta."
        ),
        model="gemini-2.5-flash",
        # Hands anything else back to whichever agent it was transferred from.
        instruction=INSTRUCTION.replace("<<PARENT>>", parent_name),
        tools=[
            FunctionTool(func=never_raise(get_my_registration)),
            FunctionTool(func=propose_action),
        ],
        # Same key as the parent: the route reads the previous reply from it,
        # whichever of the two answered.
        output_key="whatsapp_reservations_reply",
        # It only registers; it never hands the chat to another sibling.
        disallow_transfer_to_peers=True,
    )


whatsapp_registration_agent = make_registration_agent()
