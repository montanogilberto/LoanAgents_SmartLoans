"""WhatsApp Reservations Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.whatsapp_reservations.prompt import INSTRUCTION
from tools.backend_api import (
    get_available_slots, get_business_info, get_faqs, get_machine_availability, get_my_reward_points,
    get_promotions, get_reservation_hours, get_reservation_services,
)
from tools.pending_actions import propose_action
from tools.safe_tool import never_raise

whatsapp_reservations_agent = Agent(
    name="whatsapp_reservations_agent",
    description=(
        "WhatsApp assistant for a business's customers — reservations (catalog "
        "services + real free slots; PROPOSES, the backend books after \"sí\"), "
        "opening hours, the promotions the POS applies, business info (address + "
        "maps link) and FAQs, "
        "the sender's own reward points and client QR (SEND_CLIENT_QR), whether "
        "the self-service laundry machines are free right now, and "
        "handing off to a person (REQUEST_HUMAN). Answers business facts only from tools."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    # Read tools never crash the turn: a backend timeout becomes "unavailable" for the model
    # (tools/safe_tool.py). propose_action reports its own validation errors.
    tools=[
        FunctionTool(func=never_raise(get_reservation_services)),
        FunctionTool(func=never_raise(get_reservation_hours)),
        FunctionTool(func=never_raise(get_available_slots)),
        FunctionTool(func=never_raise(get_promotions)),
        FunctionTool(func=never_raise(get_business_info)),
        FunctionTool(func=never_raise(get_faqs)),
        FunctionTool(func=never_raise(get_my_reward_points)),
        FunctionTool(func=never_raise(get_machine_availability)),
        FunctionTool(func=propose_action),
    ],
    output_key="whatsapp_reservations_reply",
)
