"""WhatsApp Campaign Agent — the location-validation campaign, split from the
company agent (whatsapp_reservations) on 2026-11-12.

main.py's route picks it for every message from someone who is NOT a client of
a company running the campaign and writes about it (ad click, campaign words,
or Meta's default greeting as the first message). Clients always get the
company agent. It records every interested person (RECORD_DEMAND_SIGNAL, as
soon as the service is known) and businesses offering space
(RECORD_LOCATION_HOST); the backend writes them, never this agent.
"""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.whatsapp_campaign.prompt import INSTRUCTION
from agents.whatsapp_registration import make_registration_agent
from agents.whatsapp_reservations.agent import _use_trusted_ids
from tools.backend_api import (
    get_business_info, get_faqs, get_promotions, get_reservation_hours, get_reservation_services,
)
from tools.pending_actions import propose_action
from tools.safe_tool import never_raise

whatsapp_campaign_agent = Agent(
    name="whatsapp_campaign_agent",
    description=(
        "WhatsApp campaign for people who are not clients yet: the new laundry "
        "options being validated (autoservicio 24 h, lockers), the zone where "
        "they'd use them, their name and notice opt-in (RECORD_DEMAND_SIGNAL), "
        "and businesses offering space (RECORD_LOCATION_HOST)."
    ),
    model="gemini-2.5-flash",
    instruction=INSTRUCTION,
    tools=[
        FunctionTool(func=never_raise(get_business_info)),
        FunctionTool(func=never_raise(get_reservation_hours)),
        FunctionTool(func=never_raise(get_reservation_services)),
        FunctionTool(func=never_raise(get_promotions)),
        FunctionTool(func=never_raise(get_faqs)),
        FunctionTool(func=propose_action),
    ],
    before_tool_callback=_use_trusted_ids,
    # Same key as the company agent: both answer the same chat, and the route
    # reads the previous reply from it whichever one answered.
    output_key="whatsapp_reservations_reply",
    sub_agents=[make_registration_agent(parent_name="whatsapp_campaign_agent")],
)
