"""Structured output schema for the ticket extraction agent.

Passed as `output_schema` so Gemini emits controlled JSON. Follows
agents/evidence_validation/schema.py: no optional/default fields (the model
fills every one), enums for closed sets, and a literal "not legible" convention
instead of a guess — here via `unreadableFields` plus ""/0.0 placeholders.
"""
from enum import Enum

from pydantic import BaseModel


class PaymentType(str, Enum):
    EFECTIVO = "EFECTIVO"
    TARJETA_CREDITO = "TARJETA_CREDITO"
    TARJETA_DEBITO = "TARJETA_DEBITO"
    # A card payment where the ticket doesn't say credit vs. debit.
    TARJETA = "TARJETA"
    TRANSFERENCIA = "TRANSFERENCIA"
    CHEQUE = "CHEQUE"
    OTRO = "OTRO"
    # The ticket shows no payment information (or it is illegible).
    NO_VISIBLE = "NO_VISIBLE"


class TicketLineItem(BaseModel):
    name: str                 # description exactly as printed
    quantity: float           # as printed; 1.0 when the ticket prints no quantity
    unitPrice: float          # as printed; 0.0 when the ticket prints none
    lineTotal: float          # the printed amount for this line
    needsReview: bool         # any part of this line was blurry/ambiguous


class TicketExtractionResult(BaseModel):
    isPurchaseTicket: bool    # False for a random photo / non-receipt image
    confidence: float         # confidence in the extraction overall, 0.0-1.0
    merchantName: str
    merchantRfc: str
    ticketNumber: str
    ticketDate: str           # ISO YYYY-MM-DD, or "" when not legible
    currency: str             # ISO code, "MXN" unless the ticket shows another
    subtotal: float           # 0.0 when not printed / not legible
    tax: float                # IVA etc.; 0.0 when not printed / not legible
    total: float              # 0.0 when not legible
    paymentType: PaymentType
    paymentTypeRaw: str       # payment text exactly as printed, "" if none
    cardLast4: str            # last 4 digits if printed, "" otherwise
    lineItems: list[TicketLineItem]
    # Names of top-level fields (merchantName, ticketDate, total, ...) that
    # could not be read; their value above is the ""/0.0 placeholder.
    unreadableFields: list[str]
    notes: list[str]
