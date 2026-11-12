"""WhatsApp Reservations Agent definition."""
import inspect

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.whatsapp_registration import whatsapp_registration_agent
from agents.whatsapp_reservations.prompt import build_instruction
from tools.backend_api import (
    get_available_slots, get_business_info, get_faqs, get_machine_availability, get_my_reward_points,
    get_promotions, get_reservation_hours, get_reservation_services,
)
from tools.pending_actions import propose_action
from tools.safe_tool import never_raise


def _use_trusted_ids(tool, args, tool_context):
    """company_id / branch_id always come from the route (the WhatsApp number
    the customer wrote to), never from the model: business data now includes
    the CLABE customers send money to, so a wrong id would show another
    company's account."""
    params = inspect.signature(tool.func).parameters
    for arg, state_key in (("company_id", "wa_company_id"), ("branch_id", "wa_branch_id")):
        if arg in params:
            args[arg] = tool_context.state.get(state_key)
    return None


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
    # The campaign block is added only for the companies running it (prompt.py).
    instruction=lambda ctx: build_instruction(ctx.state.get("wa_company_id")),
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
    before_tool_callback=_use_trusted_ids,
    output_key="whatsapp_reservations_reply",
    # Same chat, same session: "quiero registrarme" is handed to it with
    # transfer_to_agent and it hands back anything else.
    sub_agents=[whatsapp_registration_agent],
)
