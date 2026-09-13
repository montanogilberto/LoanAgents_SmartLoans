"""
Smoke tests -- verifies the client follow-up agent and its tools import
cleanly and are wired correctly. Does not call Gemini or make real HTTP
requests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_client_followup_agent_importable():
    from agents.client_followup import client_followup_agent
    assert client_followup_agent.name == "client_followup_agent"
    assert client_followup_agent.output_key == "client_followup_suggestion"


def test_client_followup_agent_has_backend_tools():
    from agents.client_followup.agent import client_followup_agent
    tool_names = {t.func.__name__ for t in client_followup_agent.tools}
    assert tool_names == {"get_client_follow_ups", "get_client_loans"}


def test_prompt_exports_instruction():
    from agents.client_followup.prompt import INSTRUCTION
    assert isinstance(INSTRUCTION, str) and len(INSTRUCTION) > 20
