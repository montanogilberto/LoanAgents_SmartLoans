"""WhatsApp Insights Agent definition."""
from google.adk.agents import Agent

from agents.whatsapp_insights.prompt import INSTRUCTION

whatsapp_insights_agent = Agent(
    name="whatsapp_insights_agent",
    description=(
        "Reads a period of WhatsApp customer messages for one business and "
        "summarizes what customers want, their most common doubts and what they "
        "ask for that the business doesn't offer. Advisory, read-only, no tools."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="whatsapp_insights_result",
)
