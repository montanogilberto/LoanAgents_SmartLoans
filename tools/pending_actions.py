"""
Shared helper for POS support agents that need to PROPOSE a write action,
not just explain. Because each /support/pos-* call is a fresh, stateless
ADK session (see main.py — session_id=uuid4() every time), an agent has no
memory across turns; it can only see the user's single latest message. So
a write action can only be proposed when that one message already contains
everything required — there is no multi-turn "gather info, then propose"
flow with the current wiring (a real limitation, not a design choice; the
fix would be injecting conversation history into each call, which touches
every topic and is a separate piece of work).

The tool writes the proposal into ToolContext.state, NOT anywhere durable —
main.py reads it back from the SAME invocation's final session state
(session.state, after the run, before that in-memory session is ever
discarded) and returns it to the backend as `pendingAction`. The backend
(smartloans_backend/modules/posSupportChat.py) is what actually persists
it (in-process, with a TTL) and is the ONLY place that ever executes the
real write — this tool never calls the backend itself.
"""
from __future__ import annotations

from google.adk.tools import ToolContext


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
        {"proposed": true} — always; this tool cannot fail.
    """
    tool_context.state["pending_action"] = {
        "capability": capability,
        "fields": fields,
        "confirmationSummary": confirmation_summary,
    }
    return {"proposed": True}
