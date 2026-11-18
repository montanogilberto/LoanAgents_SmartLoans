"""Business Menu Agent definition."""
from google.adk.agents import Agent

from agents.business_menu.prompt import INSTRUCTION

business_menu_agent = Agent(
    name="business_menu_agent",
    description=(
        "At POS signup, reads the new business's menu, catalog or price list "
        "(file or web page) and summarizes what it sells and which POS modules "
        "it needs. No tools, writes nothing."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="business_menu_result",
)
