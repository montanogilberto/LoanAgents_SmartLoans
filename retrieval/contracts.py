"""
GMO Write Contracts — the machine-readable half of what the POS support
agents' prompts currently only state in prose (see e.g.
agents/pos_clients_support/prompt.py's "Creating a client directly"
section, agents/pos_income_support/prompt.py's "Creating an income record
directly", agents/pos_expenses_support/prompt.py's "Creating an expense
record directly"). Same philosophy as retrieval/graph.py: a small, real,
code-verified description — one contract per capability actually proposed
by an agent today, not an aspirational schema for a capability that
doesn't exist yet.

This exists because propose_action (tools/pending_actions.py) previously
trusted the LLM to have followed its prompt's field rules exactly —
nothing stopped it from proposing CREATE_CLIENT with a missing cellphone
or an invalid clientType, which the cashier would only discover after
confirming, one round-trip later, from the backend's own error. Validating
here turns that into an immediate correction inside the same turn — it
does not replace the backend's own validation, which still has the final
word once a cashier confirms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Union

# Fields that must NEVER be accepted from agent-supplied `fields` for ANY
# capability, regardless of what a given prompt says. smartloans_backend's
# _execute_pending_action (modules/posSupportChat.py) sets companyId/userId
# itself from the server-side pending record (the authenticated request
# that created the proposal) -- never from the LLM. If an agent's fields
# dict ever carried these, a dict-merge ordering slip in that executor
# could let an LLM-controlled value silently override the trusted one --
# e.g. writing an income record into the wrong company's ledger. Rejecting
# them here, at proposal time, means that class of bug can never reach the
# executor in the first place, no matter what a future prompt asks the
# agent to include. See agents/pos_income_support/prompt.py's "Creating an
# income record directly" section for the cashier-facing rule this backs.
_RESERVED_KEYS = frozenset({"companyId", "userId"})


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: type
    # True/False, or a predicate over the full fields dict for a field
    # that's only required conditionally (e.g. supplierId only when
    # expenseType == "general").
    required: Union[bool, Callable[[dict], bool]] = True
    enum: tuple[str, ...] | None = None
    min_digits: int | None = None   # count of digits in the value, e.g. a phone number
    min_value: float | None = None  # inclusive lower bound for int/float
    min_items: int | None = None    # inclusive lower bound for list length
    pattern: str | None = None      # full-match regex for str values, e.g. a date

    def is_required(self, fields: dict) -> bool:
        return self.required(fields) if callable(self.required) else self.required

    def check(self, fields: dict) -> str | None:
        """Returns an error message, or None if this field is absent-and-
        optional, or present and valid."""
        present = self.name in fields and fields[self.name] is not None
        if not present:
            return f"{self.name} is required" if self.is_required(fields) else None

        value = fields[self.name]
        if self.type is str and isinstance(value, (list, dict, bool)):
            return f"{self.name} must be text, got {value!r}"
        if self.type is int and (isinstance(value, bool) or not isinstance(value, int)):
            return f"{self.name} must be an integer, got {value!r}"
        if self.type is float and (isinstance(value, bool) or not isinstance(value, (int, float))):
            return f"{self.name} must be a number, got {value!r}"
        if self.type is list and not isinstance(value, list):
            return f"{self.name} must be a list, got {value!r}"
        if self.type is bool and not isinstance(value, bool):
            return f"{self.name} must be true or false, got {value!r}"

        if self.enum is not None and value not in self.enum:
            return f"{self.name} must be one of {self.enum}, got {value!r}"
        if self.min_digits is not None:
            digits = sum(1 for c in str(value) if c.isdigit())
            if digits < self.min_digits:
                return f"{self.name} must contain at least {self.min_digits} digits, got {value!r}"
        if self.min_value is not None and isinstance(value, (int, float)) and value < self.min_value:
            return f"{self.name} must be >= {self.min_value}, got {value!r}"
        if self.min_items is not None and isinstance(value, list) and len(value) < self.min_items:
            return f"{self.name} must have at least {self.min_items} item(s), got {value!r}"
        if self.pattern is not None and not re.fullmatch(self.pattern, str(value)):
            return f"{self.name} has the wrong format, got {value!r}"
        return None


@dataclass(frozen=True)
class Contract:
    capability: str
    description: str
    fields: tuple[FieldSpec, ...]

    def validate(self, fields: dict) -> list[str]:
        errors = []
        reserved_present = _RESERVED_KEYS & fields.keys()
        for key in sorted(reserved_present):
            errors.append(
                f"{key} must not be included in fields -- it is attached "
                f"server-side from the authenticated request, never from the agent"
            )
        for spec in self.fields:
            error = spec.check(fields)
            if error:
                errors.append(error)
        return errors


# One entry per capability an agent can actually propose today (see each
# agent's propose_action call site). Keep this list real — add an entry
# only once a prompt actually proposes that capability.
CONTRACTS: dict[str, Contract] = {
    "CREATE_CLIENT": Contract(
        capability="CREATE_CLIENT",
        description="agents/pos_clients_support — new client record (POS Clientes wizard step 1)",
        fields=(
            FieldSpec("first_name", str, required=True),
            FieldSpec("last_name", str, required=False),
            FieldSpec("cellphone", str, required=True, min_digits=10),
            FieldSpec("email", str, required=False),
            FieldSpec("clientType", str, required=True, enum=("borrower", "lender", "both", "lawyer", "pos")),
        ),
    ),
    "REGISTER_CLIENT": Contract(
        capability="REGISTER_CLIENT",
        description="agents/whatsapp_registration — a WhatsApp customer registers themself; "
                    "the phone and companyId come from the channel, never from the agent",
        fields=(
            # Nombre y al menos un apellido.
            FieldSpec("fullName", str, required=True, pattern=r"\s*\S+(\s+\S+)+\s*"),
        ),
    ),
    "CREATE_INCOME": Contract(
        capability="CREATE_INCOME",
        description="agents/pos_income_support — new POS sale/income record",
        fields=(
            FieldSpec("total", float, required=True, min_value=0.01),
            FieldSpec("paymentMethod", str, required=True),
            FieldSpec("clientId", int, required=True, min_value=1),
            FieldSpec("products", list, required=True, min_items=1),
            FieldSpec("paymentDate", str, required=False),
            FieldSpec("orderId", int, required=False),
        ),
    ),
    "CREATE_EXPENSE": Contract(
        capability="CREATE_EXPENSE",
        description="agents/pos_expenses_support — new general or payroll expense",
        fields=(
            FieldSpec("expenseType", str, required=True, enum=("general", "payroll")),
            FieldSpec("supplierId", int, required=lambda f: f.get("expenseType") == "general", min_value=1),
            FieldSpec("employeeId", int, required=lambda f: f.get("expenseType") == "payroll", min_value=1),
            FieldSpec("total", float, required=True, min_value=0.01),
            FieldSpec("paymentMethod", str, required=True),
            FieldSpec("notes", str, required=False),
            FieldSpec("paymentDate", str, required=False),
        ),
    ),
    "REQUEST_HUMAN": Contract(
        capability="REQUEST_HUMAN",
        description="agents/whatsapp_reservations — customer wants a person, or the agent can't help; the backend pauses the bot in that chat and pushes the POS (no confirmation step)",
        fields=(
            FieldSpec("reason", str, required=True),
        ),
    ),
    "SEND_CLIENT_QR": Contract(
        capability="SEND_CLIENT_QR",
        description="agents/whatsapp_reservations — the sender's own client QR (CLIENT:<id>:<name>, the one the POS scanner reads); the backend matches the client by the WhatsApp phone and sends the image (no confirmation step)",
        fields=(),
    ),
    "RECORD_DEMAND_SIGNAL": Contract(
        capability="RECORD_DEMAND_SIGNAL",
        description="agents/whatsapp_reservations — campaign: a person's interest in a service concept "
                    "and the zone where they'd use it (smartloans_backend dbo.demandSignals). Upserted per "
                    "(company, phone, concept), so it can be called again as more answers come in. No "
                    "confirmation step; phone, company and channel come from the WhatsApp channel, never "
                    "from the agent. Value lists must match the SQL CHECK constraints.",
        fields=(
            FieldSpec("concept", str, required=True, enum=("autoservicio_24h", "lockers", "qr_gmo")),
            # Optional since 2026-11-12: every interested person is stored as soon as
            # the concept is known (smartloans_backend 2026-11-12_demandSignals_zoneOptional.sql).
            FieldSpec("preferredZoneText", str, required=False, pattern=r"\s*\S.{0,118}"),
            FieldSpec("residenceZoneText", str, required=False, pattern=r"\s*\S.{0,118}"),
            FieldSpec("workZoneText", str, required=False, pattern=r"\s*\S.{0,118}"),
            FieldSpec("frequency", str, required=False, enum=("semanal", "quincenal", "mensual", "ocasional")),
            FieldSpec("preferredTime", str, required=False,
                      enum=("temprano", "dia", "tarde", "noche", "madrugada", "fin_de_semana")),
            FieldSpec("currentSolution", str, required=False,
                      enum=("lavadora_propia", "lavanderia", "familiar", "otro")),
            FieldSpec("painPoint", str, required=False, pattern=r"(?s)\s*\S.{0,198}"),
            FieldSpec("wouldUse", str, required=False, enum=("si", "tal_vez", "no")),
            FieldSpec("notifyOptIn", bool, required=False),
            FieldSpec("prospectName", str, required=False, pattern=r"\s*\S.{0,118}"),
            # Clean colonia/sector name for the demand map; preferredZoneText keeps their words.
            FieldSpec("preferredZoneName", str, required=False, pattern=r"\s*\S.{0,118}"),
        ),
    ),
    "RECORD_LOCATION_HOST": Contract(
        capability="RECORD_LOCATION_HOST",
        description="agents/whatsapp_reservations — Red GMO: a business offering space for a GMO locker "
                    "(smartloans_backend dbo.locationHosts). Upserted per (company, phone, businessName), "
                    "so it can be called again as more answers come in. No confirmation step; phone, "
                    "company and channel come from the WhatsApp channel, never from the agent. Value lists "
                    "must match the SQL CHECK constraints.",
        fields=(
            FieldSpec("businessName", str, required=True, pattern=r"\s*\S.{0,118}"),
            FieldSpec("zoneText", str, required=True, pattern=r"\s*\S.{0,118}"),
            FieldSpec("zoneName", str, required=False, pattern=r"\s*\S.{0,118}"),
            FieldSpec("businessType", str, required=False,
                      enum=("tienda", "gimnasio", "farmacia", "oficina", "escuela", "gasolinera",
                            "restaurante", "otro")),
            FieldSpec("contactName", str, required=False, pattern=r"\s*\S.{0,118}"),
            FieldSpec("contactRole", str, required=False, enum=("dueno", "encargado", "otro")),
            FieldSpec("addressText", str, required=False, pattern=r"\s*\S.{0,198}"),
            FieldSpec("openingHours", str, required=False, pattern=r"\s*\S.{0,118}"),
            FieldSpec("opensWeekends", bool, required=False),
            FieldSpec("spaceType", str, required=False,
                      enum=("interior", "exterior", "estacionamiento", "no_seguro")),
            FieldSpec("dailyVisitors", str, required=False,
                      enum=("menos_50", "50_200", "mas_200", "no_seguro")),
            FieldSpec("hasParking", bool, required=False),
            FieldSpec("contactOptIn", bool, required=False),
        ),
    ),
    "CREATE_RESERVATION": Contract(
        capability="CREATE_RESERVATION",
        description="agents/whatsapp_reservations — slot for a catalog service booked by a customer over WhatsApp",
        fields=(
            FieldSpec("reservationServiceId", int, required=True, min_value=1),
            FieldSpec("reservationDate", str, required=True, pattern=r"\d{4}-\d{2}-\d{2}"),
            FieldSpec("timeSlot", str, required=True, pattern=r"\d{2}:\d{2}"),
            FieldSpec("clientName", str, required=True),
            FieldSpec("serviceDetail", str, required=False),
            FieldSpec("notes", str, required=False),
        ),
    ),
    "PICKUP_AGREEMENT": Contract(
        capability="PICKUP_AGREEMENT",
        description="agents/voice_secretary — how and when a customer gets a finished order, agreed on a phone call; the backend asks the customer to confirm it by WhatsApp",
        fields=(
            FieldSpec("deliveryMode", str, required=True, enum=("pickup", "delivery")),
            # The day the customer agreed to, said aloud; checked against `date` by
            # agents/voice_secretary/calendar_check.py (the model mapped weekdays wrong).
            FieldSpec("weekday", str, required=True,
                      enum=("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")),
            FieldSpec("date", str, required=True, pattern=r"\d{4}-\d{2}-\d{2}"),
            FieldSpec("time", str, required=True, pattern=r"([01]\d|2[0-3]):[0-5]\d"),
            FieldSpec("address", str, required=lambda f: f.get("deliveryMode") == "delivery"),
            FieldSpec("paymentMethod", str, required=False),
            FieldSpec("notes", str, required=False),
        ),
    ),
}


def describe_schema() -> str:
    """Renders CONTRACTS as short lines for a system prompt — same pattern
    as retrieval/graph.py's describe_schema(), so a prompt can reference the
    real, current field rules instead of a hand-written, driftable copy."""
    lines = []
    for c in CONTRACTS.values():
        required = [f.name for f in c.fields if f.required is True]
        conditional = [f.name for f in c.fields if callable(f.required)]
        optional = [f.name for f in c.fields if f.required is False]
        parts = [f"required: {', '.join(required) or '—'}"]
        if conditional:
            parts.append(f"conditionally required: {', '.join(conditional)}")
        if optional:
            parts.append(f"optional: {', '.join(optional)}")
        lines.append(f"- {c.capability} ({c.description}) — {'; '.join(parts)}")
    return "\n".join(lines)
