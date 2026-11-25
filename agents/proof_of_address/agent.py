"""Reads the document only; the comparison with the INE is code (proof_documents/)."""
from google.adk.agents import Agent
from google.genai import types

from agents.proof_of_address.prompt import INSTRUCTION
from agents.proof_of_address.schema import ProofOfAddress

proof_of_address_agent = Agent(
    name="proof_of_address_agent",
    description="Transcribes holder, address and date from a utility bill. Never sees the INE and never decides.",
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_schema=ProofOfAddress,
    generate_content_config=types.GenerateContentConfig(temperature=0.0, top_p=0.1, top_k=1),
)
