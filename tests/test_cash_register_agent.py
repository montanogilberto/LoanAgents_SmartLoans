"""
Smoke tests -- verifies the cash register agent and its tools import cleanly
and are wired correctly. Does not call Gemini or make real HTTP requests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_cash_register_agent_importable():
    from agents.cash_register import cash_register_agent
    assert cash_register_agent.name == "cash_register_agent"
    assert cash_register_agent.output_key == "cash_register_review"


def test_cash_register_agent_has_backend_tools():
    from agents.cash_register.agent import cash_register_agent
    tool_names = {t.func.__name__ for t in cash_register_agent.tools}
    assert tool_names == {"get_cash_register_daily_summary", "list_cash_register_movements"}


def test_prompt_exports_instruction():
    from agents.cash_register.prompt import INSTRUCTION
    assert isinstance(INSTRUCTION, str) and len(INSTRUCTION) > 20
