"""
never_raise — a read tool that fails tells the model so, instead of crashing
the whole agent turn.

Found 2026-11-02: a customer wrote "cita" right after smartloans_backend
restarted; get_reservation_services timed out (httpx.ReadTimeout, 15 s), the
exception escaped the tool, the route returned 500 and the backend sent the
customer "Perdón, tuve un problema para responderte". The prompt already says
"when a tool fails, say briefly you couldn't check that right now" — the model
just never got the chance.

Only for READ tools: a write must surface its error, not be retried or hidden.
"""
import functools
import logging

logger = logging.getLogger(__name__)

UNAVAILABLE = {
    "status": "unavailable",
    "error": "No se pudo consultar en este momento (el sistema tardó demasiado o falló). "
             "No es un dato del negocio: no digas que no hay; ofrece intentarlo de nuevo en un momento.",
}


def never_raise(func):
    """Wrap a sync read tool: any exception → UNAVAILABLE (logged). functools.wraps keeps
    the name, docstring and signature (incl. tool_context) that ADK builds the declaration from."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — any failure of a read tool is reported, never raised
            logger.warning("[tool] %s failed: %s: %s", func.__name__, type(e).__name__, str(e)[:200])
            return dict(UNAVAILABLE)
    return wrapper
