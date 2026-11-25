"""Collections WhatsApp Agent — copy of the whatsapp_reservations pattern,
independent from it. Proposes only; the backend executes after the customer's "sí"."""
from google.adk.agents import Agent
from google.adk.tools import FunctionTool, ToolContext

from agents.collections_whatsapp.prompt import INSTRUCTION
from loan_collections.situation import check_promise, hermosillo_now
from tools.pending_actions import propose_action as _propose_action


def get_my_loan_situation(tool_context: ToolContext) -> dict:
    """The current situation of the sender's own loan. Takes no arguments.

    Returns:
        {"status": "overdue"|"current"|"settled", ...figures} or
        {"status": "hold"} when no figure can be given.
    """
    s = tool_context.state.get("coll_situation") or {"status": "hold"}
    return {"status": "hold"} if s.get("status") == "hold" else dict(s)


def propose_action(capability: str, fields: dict, confirmation_summary: str, tool_context: ToolContext) -> dict:
    """Records a proposal. It writes nothing.

    Args:
        capability: "PROMISE_TO_PAY" or "REQUEST_HUMAN".
        fields: PROMISE_TO_PAY: amount (number), weekday, date (from nextDays).
            REQUEST_HUMAN: reason.
        confirmation_summary: Short summary for the customer.

    Returns:
        {"proposed": true}, or {"proposed": false, "errors": [...]}.
    """
    if capability not in ("PROMISE_TO_PAY", "REQUEST_HUMAN"):
        return {"proposed": False, "errors": ["capability must be PROMISE_TO_PAY or REQUEST_HUMAN"]}
    if capability == "PROMISE_TO_PAY":
        errors = check_promise(fields or {}, tool_context.state.get("coll_situation") or {}, hermosillo_now().date())
        if errors:
            return {"proposed": False, "errors": errors}
    return _propose_action(capability, fields, confirmation_summary, tool_context)


collections_whatsapp_agent = Agent(
    name="collections_whatsapp_agent",
    description=("WhatsApp collections: tells a borrower their own loan situation and PROPOSES a "
                 "PROMISE_TO_PAY or REQUEST_HUMAN. Never confirms a payment."),
    model="gemini-2.5-flash",
    instruction=lambda _ctx: INSTRUCTION,
    tools=[FunctionTool(func=get_my_loan_situation), FunctionTool(func=propose_action)],
)
