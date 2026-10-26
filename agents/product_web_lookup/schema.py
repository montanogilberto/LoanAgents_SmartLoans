"""Shape of the web lookup agent's JSON reply.

Not passed as output_schema (incompatible with the search tool): the model's
text is parsed into this and normalized in reconcile.py. Defaults keep a
partial or malformed reply usable instead of failing the request.
"""
from pydantic import BaseModel


class WebProductResult(BaseModel):
    found: bool = False
    fullName: str = ""         # product title as the source shows it
    brand: str = ""
    productName: str = ""      # what it is ("Agua Purificada"), without brand/variant
    variant: str = ""          # flavor / scent / presentation, "" if none
    description: str = ""      # 1-2 Spanish sentences from the source
    netContent: float = 0.0    # per piece (500 for a pack of 45 × 500 ml)
    unitOfMeasure: str = ""    # g | kg | ml | L | pz | ""
    unitsPerPack: int = 1      # pieces in what the ticket line bought
    price: float = 0.0         # price shown by the source, 0.0 if none
    codeConfirmed: bool = False  # a source shows this exact item code for the product
    notes: list[str] = []
