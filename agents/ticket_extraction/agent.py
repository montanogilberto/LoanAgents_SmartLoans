"""Ticket Extraction Agent definition."""
from google.adk.agents import Agent
from google.genai import types

from agents.ticket_extraction.prompt import INSTRUCTION
from agents.ticket_extraction.schema import TicketExtractionResult

ticket_extraction_agent = Agent(
    name="ticket_extraction_agent",
    description=(
        "Reads a photo of an expense ticket/receipt via Gemini vision and "
        "extracts merchant, date, total, type of payment and per-product "
        "line items to pre-fill the Nuevo Egreso form — never guesses an "
        "illegible value and never creates or edits an expense itself."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="ticket_extraction_result",
    output_schema=TicketExtractionResult,
    # Near-deterministic: the same ticket should give the same numbers.
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.1,
        top_k=1,
    ),
)
