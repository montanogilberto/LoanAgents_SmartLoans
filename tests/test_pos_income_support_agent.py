"""
Smoke tests -- verifies the POS income support agent and its tools import
cleanly and are wired correctly (same pattern as test_expense_agent.py etc.).

Plus one real, live-backend regression test for the actual bug found
2026-09-15: get_monthly_income() used to return income[0] -- one arbitrary
transaction row -- because /monthly_income returns every transaction for
the month, not a pre-aggregated summary. The agent correctly refused to
report an untrustworthy number, so cashiers asking "ingresos de hoy" got
"no tengo datos" despite real income existing. Fixed by aggregating
client-side in tools/backend_api.py::get_monthly_income.
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_pos_income_support_agent_importable():
    from agents.pos_income_support.agent import pos_income_support_agent
    assert pos_income_support_agent.name == "pos_income_support_agent"
    assert pos_income_support_agent.output_key == "pos_income_support_reply"


def test_pos_income_support_agent_has_expected_tools():
    from agents.pos_income_support.agent import pos_income_support_agent
    tool_names = {
        t.func.__name__ for t in pos_income_support_agent.tools if hasattr(t, "func")
    }
    assert tool_names == {"get_monthly_income", "hybrid_search", "propose_action"}


def test_prompt_exports_instruction():
    from agents.pos_income_support.prompt import INSTRUCTION
    assert isinstance(INSTRUCTION, str) and len(INSTRUCTION) > 20


def _backend_reachable() -> bool:
    from config.settings import SMARTLOANS_BACKEND_URL
    try:
        httpx.post(f"{SMARTLOANS_BACKEND_URL}/monthly_income",
                   json={"income": [{"companyId": 1}]}, timeout=10.0).raise_for_status()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _backend_reachable(), reason="smartloans_backend not reachable")
def test_get_monthly_income_returns_an_aggregate_not_one_raw_row():
    """Regression test for the actual bug: the return value must be a
    computed aggregate (monthlyTotal/monthlyCount/todayTotal/todayCount),
    never a raw transaction row (which would carry incomeId/paymentMethod/
    clientId instead)."""
    from tools.backend_api import get_monthly_income

    result = get_monthly_income(1)

    assert set(result.keys()) == {
        "companyId", "monthlyTotal", "monthlyCount", "todayTotal", "todayCount",
    }
    assert "incomeId" not in result, "must not leak a raw transaction row"
    assert result["monthlyCount"] >= result["todayCount"] >= 0
    assert result["monthlyTotal"] >= result["todayTotal"] >= 0
    # Company 1 has real recorded income as of 2026-09 (verified via a
    # direct curl against the live endpoint) -- if this ever legitimately
    # drops to zero, this assertion should be revisited, not silently
    # loosened.
    assert result["monthlyCount"] > 0
