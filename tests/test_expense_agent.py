"""
Smoke tests -- verifies the expense agent and its tools import cleanly and
are wired correctly. Does not call Gemini or make real HTTP requests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_expense_agent_importable():
    from agents.expense import expense_agent
    assert expense_agent.name == "expense_agent"
    assert expense_agent.output_key == "expense_categorization"


def test_expense_agent_has_backend_tools():
    from agents.expense.agent import expense_agent
    tool_names = {t.func.__name__ for t in expense_agent.tools}
    assert tool_names == {"get_recent_expenses"}


def test_prompt_exports_instruction():
    from agents.expense.prompt import INSTRUCTION
    assert isinstance(INSTRUCTION, str) and len(INSTRUCTION) > 20
