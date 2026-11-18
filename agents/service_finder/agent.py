"""Service Finder Agent definition."""
from google.adk.agents import Agent

from agents.service_finder.prompt import INSTRUCTION

service_finder_agent = Agent(
    name="service_finder_agent",
    description=(
        "On the signup page, finds out which service a visitor wants from what "
        "they write, asking at most a couple of short questions. No tools, writes nothing."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="service_finder_result",
)
