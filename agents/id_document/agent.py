"""Id Document Agent definition."""
from google.adk.agents import Agent

from agents.id_document.prompt import INSTRUCTION

id_document_agent = Agent(
    name="id_document_agent",
    description=(
        "Reads photos of a Mexican INE (voter ID) and extracts identity "
        "fields (nombre, domicilio, CURP, clave de elector, fecha de "
        "nacimiento) for KYC via Gemini vision. Never guesses or computes "
        "a field it can't actually read in the image."
    ),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_key="id_extraction_result",
)
