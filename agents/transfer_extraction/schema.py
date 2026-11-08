"""Structured output of the transfer receipt reader. No optional fields: anything
unreadable comes back empty (or 0) rather than guessed."""
from pydantic import BaseModel


class TransferExtraction(BaseModel):
    isTransferReceipt: bool
    confidence: float      # how sure the reading is, 0.0-1.0
    amountMXN: float       # 0 when not legible
    claveRastreo: str      # "" when not legible
    transferDate: str      # YYYY-MM-DD, "" when not legible
    bankFrom: str          # the sender's bank, "" when not legible
    issues: list[str]      # short Spanish notes for the cashier
