"""
Tests for retrieval/contracts.py's write-contract validation, in particular
the reserved-key guard added 2026-09-18: companyId/userId must never be
accepted inside an agent-proposed `fields` dict, because
smartloans_backend/modules/posSupportChat.py::_execute_pending_action
attaches the real (authenticated, server-side) companyId/userId itself.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from retrieval.contracts import CONTRACTS


def test_create_income_valid_fields_pass():
    errors = CONTRACTS["CREATE_INCOME"].validate({
        "total": 250,
        "paymentMethod": "Tarjeta",
        "clientId": 1,
        "products": [{"productId": 1002, "quantity": 1}],
    })
    assert errors == []


def test_create_income_requires_products_non_empty():
    errors = CONTRACTS["CREATE_INCOME"].validate({
        "total": 250, "paymentMethod": "Tarjeta", "clientId": 1, "products": [],
    })
    assert any("products" in e for e in errors)


def test_create_income_requires_products_present():
    errors = CONTRACTS["CREATE_INCOME"].validate({
        "total": 250, "paymentMethod": "Tarjeta", "clientId": 1,
    })
    assert any("products" in e for e in errors)


def test_create_income_rejects_agent_supplied_company_id():
    errors = CONTRACTS["CREATE_INCOME"].validate({
        "total": 250, "paymentMethod": "Tarjeta", "clientId": 1,
        "products": [{"productId": 1002, "quantity": 1}],
        "companyId": 999,
    })
    assert any("companyId" in e for e in errors)


def test_create_income_rejects_agent_supplied_user_id():
    errors = CONTRACTS["CREATE_INCOME"].validate({
        "total": 250, "paymentMethod": "Tarjeta", "clientId": 1,
        "products": [{"productId": 1002, "quantity": 1}],
        "userId": 15,
    })
    assert any("userId" in e for e in errors)


def test_reserved_key_guard_applies_to_every_capability_not_just_income():
    for capability, contract in CONTRACTS.items():
        errors = contract.validate({"companyId": 1})
        assert any("companyId" in e for e in errors), (
            f"{capability} contract does not reject a companyId key in fields"
        )
