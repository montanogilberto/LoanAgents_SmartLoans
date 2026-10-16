"""WhatsApp Reservations Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.whatsapp_reservations.prompt import INSTRUCTION
from tools.backend_api import (
    get_available_slots, get_business_info, get_faqs, get_my_reward_points,
    get_promotions, get_reservation_hours, get_reservation_services,
)
from tools.pending_actions import propose_action

whatsapp_reservations_agent = Agent(
    name="whatsapp_reservations_agent",
    description=(
        "WhatsApp assistant for a business's customers — reservations (catalog "
        "services + real free slots; PROPOSES, the backend books after \"sí\"), "
        "opening hours, the promotions the POS applies, business info and FAQs, "
        "the sender's own reward points, and handing off to a person "
        "(REQUEST_HUMAN). Answers business facts only from tools."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_reservation_services),
        FunctionTool(func=get_reservation_hours),
        FunctionTool(func=get_available_slots),
        FunctionTool(func=get_promotions),
        FunctionTool(func=get_business_info),
        FunctionTool(func=get_faqs),
        FunctionTool(func=get_my_reward_points),
        FunctionTool(func=propose_action),
    ],
    output_key="whatsapp_reservations_reply",
)
