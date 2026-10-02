"""WhatsApp Reservations Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.whatsapp_reservations.prompt import INSTRUCTION
from tools.backend_api import get_available_slots, get_reservation_hours, get_reservation_services
from tools.pending_actions import propose_action

whatsapp_reservations_agent = Agent(
    name="whatsapp_reservations_agent",
    description=(
        "WhatsApp assistant for a business's customers — shows the services "
        "from its catalog and free slots from the real availability calendar, "
        "and PROPOSES a reservation once service, day, time and name are known (never books "
        "it itself — smartloans_backend/modules/whatsappReservations.py does "
        "that only after the customer replies \"sí\")."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[
        FunctionTool(func=get_reservation_services),
        FunctionTool(func=get_reservation_hours),
        FunctionTool(func=get_available_slots),
        FunctionTool(func=propose_action),
    ],
    output_key="whatsapp_reservations_reply",
)
