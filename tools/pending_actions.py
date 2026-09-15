"""
Shared helper for POS support agents that need to PROPOSE a write action,
not just explain. Each /support/pos-* call reuses the SAME ADK session for
a given (topic, conversationId) — see main.py's _get_or_create_pos_session
— so the agent DOES see prior turns of this conversation and can gather
required fields across several messages before proposing (e.g. ask for the
product, then the payment method, then propose). Only a server restart
(in-memory session store) or a "clear history"/new-conversation action
resets this.

The tool writes the proposal into ToolContext.state, NOT anywhere durable —
main.py reads it back from the SAME invocation's final session state
(session.state, after the run, before that in-memory session is ever
discarded) and returns it to the backend as `pendingAction`. The backend
(smartloans_backend/modules/posSupportChat.py) is what actually persists
it (in-process, with a TTL) and is the ONLY place that ever executes the
real write — this tool never calls the backend itself.

Before writing the proposal, `fields` is checked against
retrieval/contracts.py's CONTRACTS for this capability — the
machine-readable version of the required-field rules each agent's prompt
states in prose. This exists because an LLM occasionally drifts from its
own prompt (a missing digit, a made-up clientType); catching that here
surfaces it to the cashier in the SAME turn instead of one round-trip
later, after they've already confirmed, as a backend error.
"""
from __future__ import annotations

from google.adk.tools import ToolContext

from retrieval.contracts import CONTRACTS


def propose_action(capability: str, fields: dict, confirmation_summary: str, tool_context: ToolContext) -> dict:
    """Records a proposed write action for this turn. Call this ONLY when
    you have every required field from the user's message — never propose
    with a guessed or missing value.

    Args:
        capability: The exact capability name being proposed, e.g. "CREATE_CLIENT".
        fields: The validated fields to use if confirmed.
        confirmation_summary: A short, human-readable summary to show the
            user, asking them to confirm (you should relay this back to
            them as your reply).

    Returns:
        {"proposed": true} on success.
        {"proposed": false, "errors": [...]} if `fields` fails the
        capability's contract (retrieval/contracts.py) — when this happens,
        do NOT tell the user it was proposed; ask them for a corrected value
        for whichever field the error names, then call this tool again.
    """
    contract = CONTRACTS.get(capability)
    if contract is not None:
        errors = contract.validate(fields)
        if errors:
            return {"proposed": False, "errors": errors}

    tool_context.state["pending_action"] = {
        "capability": capability,
        "fields": fields,
        "confirmationSummary": confirmation_summary,
    }
    return {"proposed": True}
