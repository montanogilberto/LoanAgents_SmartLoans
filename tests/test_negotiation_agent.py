"""
Smoke tests -- verifies the negotiation agent and its tools import cleanly
and are wired correctly. Does not call Gemini or make real HTTP requests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_negotiation_agent_importable():
    from agents.negotiation import negotiation_agent
    assert negotiation_agent.name == "negotiation_agent"
    assert negotiation_agent.output_key == "negotiation_reply"


def test_negotiation_agent_has_backend_tools():
    from agents.negotiation.agent import negotiation_agent
    tool_names = {t.func.__name__ for t in negotiation_agent.tools}
    assert tool_names == {
        "get_conversation",
        "get_client_loans",
        "get_client_dashboard",
        "get_credit_score",
    }


def test_root_agent_is_negotiation_agent():
    from agents import root_agent
    assert root_agent.name == "negotiation_agent"


def test_backend_api_functions_are_pure_http_no_db_imports():
    """Verify tools/backend_api.py never imports backend DB modules directly —
    this repo must stay independent from smartloans_backend, API-only."""
    import tools.backend_api as backend_api
    import inspect

    source = inspect.getsource(backend_api)
    for forbidden in ("import pymssql", "import pyodbc", "from databases", "from modules"):
        assert forbidden not in source, f"backend_api.py must not import backend internals ({forbidden})"


def test_prompt_exports_instruction():
    from agents.negotiation.prompt import INSTRUCTION
    assert isinstance(INSTRUCTION, str) and len(INSTRUCTION) > 20
