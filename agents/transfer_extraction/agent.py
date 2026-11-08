"""Transfer receipt reader: pre-fills the cashier's form, never credits money."""
from google.adk.agents import Agent
from google.genai import types

from agents.transfer_extraction.prompt import INSTRUCTION
from agents.transfer_extraction.schema import TransferExtraction

transfer_extraction_agent = Agent(
    name="transfer_extraction_agent",
    description="Reads total, clave de rastreo, date and sending bank from a SPEI receipt photo.",
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="transfer_extraction_result",
    output_schema=TransferExtraction,
    generate_content_config=types.GenerateContentConfig(temperature=0.0, top_p=0.1, top_k=1),
)
