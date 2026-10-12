"""Structured output schema for the product label extraction agent.

Passed as `output_schema` so Gemini emits controlled JSON. Same conventions as
agents/ticket_extraction/schema.py: no optional/default fields (the model fills
every one), an enum for the closed set of units, and ""/0.0 placeholders plus
`unreadableFields` instead of a guess.
"""
from enum import Enum

from pydantic import BaseModel


class UnitOfMeasure(str, Enum):
    # Exactly the units the POS "Nuevo Producto" form offers.
    G = "g"
    KG = "kg"
    ML = "ml"
    L = "L"
    PZ = "pz"
    # No net content printed, or illegible.
    NO_VISIBLE = "NO_VISIBLE"


class ProductLabelResult(BaseModel):
    isProduct: bool           # False for a random photo that is not a product/package
    confidence: float         # confidence in the extraction overall, 0.0-1.0
    brand: str                # brand as printed ("Electrolit"), "" if none
    productName: str          # what the product is, as printed ("Suero Rehidratante")
    variant: str              # flavor / scent / color / presentation ("Manzana"), "" if none
    description: str          # 1-2 sentence Spanish description built ONLY from label text
    barCode: str              # digits exactly as printed under the barcode, "" if not visible
    netContent: float         # the number of the net content ("1000"), 0.0 if not visible
    unitOfMeasure: UnitOfMeasure
    netContentRaw: str        # net content exactly as printed ("Frasco con 1000 mL"), "" if none
    expirationDate: str       # ISO YYYY-MM-DD, or "" when not printed / not legible
    expirationRaw: str        # caducidad text exactly as printed, "" if none
    lotNumber: str            # lote as printed, "" if none
    manufacturer: str         # maker/distributor as printed ("Laboratorios PiSA"), "" if none
    # Names of fields above that are on the package but could not be read.
    unreadableFields: list[str]
    notes: list[str]
