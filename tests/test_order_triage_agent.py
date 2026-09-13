"""
Smoke tests -- verifies the order triage agent and its tools import cleanly
and are wired correctly. Does not call Gemini or make real HTTP requests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_order_triage_agent_importable():
    from agents.order_triage import order_triage_agent
    assert order_triage_agent.name == "order_triage_agent"
    assert order_triage_agent.output_key == "order_triage_result"


def test_order_triage_agent_has_backend_tools():
    from agents.order_triage.agent import order_triage_agent
    tool_names = {t.func.__name__ for t in order_triage_agent.tools}
    assert tool_names == {"list_open_orders"}


def test_prompt_exports_instruction():
    from agents.order_triage.prompt import INSTRUCTION
    assert isinstance(INSTRUCTION, str) and len(INSTRUCTION) > 20
