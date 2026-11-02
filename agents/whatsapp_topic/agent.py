"""WhatsApp Topic Agent definition."""
from google.adk.agents import Agent

from agents.whatsapp_topic.prompt import INSTRUCTION

whatsapp_topic_agent = Agent(
    name="whatsapp_topic_agent",
    description=(
        "Tags one WhatsApp customer message with what the customer wants, from a "
        "fixed topic list, so the POS can count the most frequent questions. "
        "No tools, never answers the customer."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="whatsapp_topic_result",
)
