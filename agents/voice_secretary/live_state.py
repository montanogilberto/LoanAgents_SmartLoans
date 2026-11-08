"""
What the bridge really heard on a live call, for the agent's tools to check.

The model's own account of the call is not evidence: on a real call (2026-11-07)
the customer's reply was transcribed as "<noise>" and the model still passed
"sí" to confirm_customer_identity. The bridge (voice/twilio_bridge.py) records the
customer's transcribed speech here, keyed by the ADK session id, and the tools
read it back. In-process only — one entry per live call, removed when it ends.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Call:
    customer: list[tuple[float, str]] = field(default_factory=list)   # (monotonic time, text)
    proposed_at: float | None = None
    asked_anything_else: bool = False     # end_call has already held the call once


_CALLS: dict[str, _Call] = {}


def register(session_id: str) -> None:
    _CALLS[session_id] = _Call()


def unregister(session_id: str) -> None:
    _CALLS.pop(session_id, None)


def is_live(session_id: str | None) -> bool:
    return session_id in _CALLS


def add_customer_text(session_id: str, text: str, at: float | None = None) -> None:
    call = _CALLS.get(session_id)
    if call is not None and text:
        call.customer.append((at if at is not None else time.monotonic(), text))


def customer_text_since(session_id: str, since: float) -> str:
    call = _CALLS.get(session_id)
    return "".join(t for at, t in call.customer if at >= since).strip() if call else ""


def mark_proposal(session_id: str, at: float | None = None) -> None:
    call = _CALLS.get(session_id)
    if call is not None:
        call.proposed_at = at if at is not None else time.monotonic()


def may_end_call(session_id: str) -> bool:
    """False only the FIRST time the agent tries to hang up right after an
    agreement, before the customer has said anything since. Never twice — a
    silent customer must not keep the call open."""
    call = _CALLS.get(session_id)
    if call is None or call.proposed_at is None or call.asked_anything_else:
        return True
    if any(at > call.proposed_at for at, _ in call.customer):
        return True
    call.asked_anything_else = True
    return False


def session_id_of(tool_context) -> str | None:
    try:
        return tool_context._invocation_context.session.id   # ADK keeps no public accessor
    except AttributeError:
        return None
