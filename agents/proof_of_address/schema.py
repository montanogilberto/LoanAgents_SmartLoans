"""No optional fields: anything unreadable comes back "" rather than guessed."""
from typing import Literal

from pydantic import BaseModel


class ProofOfAddress(BaseModel):
    documentType: Literal["ELECTRICITY", "WATER", "GAS", "INTERNET", "PHONE", "PAYROLL", "OTHER"]
    issuer: str
    holderName: str
    street: str
    exteriorNumber: str
    interiorNumber: str
    neighborhood: str
    postalCode: str
    municipality: str
    state: str
    issueDate: str       # YYYY-MM-DD
    billingMonth: str    # YYYY-MM
    dueDate: str         # YYYY-MM-DD
    confidence: float
    issues: list[str]
    signsOfEditing: bool
