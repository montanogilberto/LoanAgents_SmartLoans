"""Product Label Extraction Agent definition."""
from google.adk.agents import Agent
from google.genai import types

from agents.product_extraction.prompt import INSTRUCTION
from agents.product_extraction.schema import ProductLabelResult

product_extraction_agent = Agent(
    name="product_extraction_agent",
    description=(
        "Reads photos of a physical product (front/back label) via Gemini "
        "vision and extracts brand, name, variant, description, barcode, net "
        "content and expiration to pre-fill the POS Nuevo Producto form — "
        "never guesses an illegible value and never creates a product itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="product_extraction_result",
    output_schema=ProductLabelResult,
    # Near-deterministic: the same label should give the same values.
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.1,
        top_k=1,
    ),
)
