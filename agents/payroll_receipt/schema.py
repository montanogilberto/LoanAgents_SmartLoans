"""No optional fields: anything unreadable comes back "" (or 0) rather than guessed."""
from typing import Literal

from pydantic import BaseModel


class PayrollReceipt(BaseModel):
    documentType: Literal["PAYROLL", "OTHER"]
    employerName: str
    workerName: str
    curp: str
    rfc: str
    payDate: str          # YYYY-MM-DD
    periodStart: str      # YYYY-MM-DD
    periodEnd: str        # YYYY-MM-DD
    payFrequency: Literal["WEEKLY", "BIWEEKLY_14", "SEMIMONTHLY", "MONTHLY", "OTHER", "UNKNOWN"]
    grossMXN: float
    deductionsMXN: float
    netMXN: float
    fiscalFolio: str      # CFDI UUID
    confidence: float
    issues: list[str]
    signsOfEditing: bool
