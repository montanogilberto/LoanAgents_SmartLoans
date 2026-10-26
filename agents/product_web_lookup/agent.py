"""Product Web Lookup Agent definition."""
from google.adk.agents import Agent
from google.adk.tools import google_search
from google.genai import types

from agents.product_web_lookup.prompt import INSTRUCTION

# No output_schema: Gemini can't combine controlled JSON with the search tool, so
# the reply is JSON text validated in code (reconcile.py, schema.py).
product_web_lookup_agent = Agent(
    name="product_web_lookup_agent",
    description=(
        "Searches the web for the retail product behind a purchase-ticket line "
        "(store item code + abbreviated description) and returns its full name, "
        "brand, net content and pieces per pack to pre-fill a new product — "
        "only what a source states, and never creates the product itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[google_search],
    generate_content_config=types.GenerateContentConfig(temperature=0.0),
)
