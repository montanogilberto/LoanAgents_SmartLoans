"""Reads the document only; the comparison with the INE is code (proof_documents/)."""
from google.adk.agents import Agent
from google.genai import types

from agents.payroll_receipt.prompt import INSTRUCTION
from agents.payroll_receipt.schema import PayrollReceipt

payroll_receipt_agent = Agent(
    name="payroll_receipt_agent",
    description="Transcribes worker, CURP, RFC, dates and totals from a payslip. Never sees the INE and never decides.",
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    output_schema=PayrollReceipt,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0, top_p=0.1, top_k=1, media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
    ),
)
