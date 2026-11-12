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


def test_record_demand_signal_contract():
    from retrieval.contracts import CONTRACTS
    c = CONTRACTS["RECORD_DEMAND_SIGNAL"]
    assert c.validate({"concept": "lockers", "preferredZoneText": "por la Pitic"}) == []
    full = {"concept": "autoservicio_24h", "preferredZoneText": "Villa de Seris",
            "residenceZoneText": "Villa de Seris", "frequency": "semanal", "preferredTime": "fin_de_semana",
            "currentSolution": "lavanderia", "painPoint": "Siempre está lleno", "wouldUse": "si",
            "notifyOptIn": True}
    assert c.validate(full) == []
    # Zone optional since 2026-11-12: every interested person is stored once the concept is known.
    assert c.validate({"concept": "lockers"}) == []
    assert c.validate({"preferredZoneText": "por la Pitic"})  # concept still required
    assert c.validate({"concept": "tintoreria", "preferredZoneText": "x"})
    assert c.validate({"concept": "lockers", "preferredZoneText": "x", "preferredTime": "mañana"})
    assert c.validate({"concept": "lockers", "preferredZoneText": "x", "notifyOptIn": "sí"})
    assert c.validate({"concept": "lockers", "preferredZoneText": "x", "companyId": 1008})
    assert c.validate({"concept": "lockers", "preferredZoneText": "   "})


def test_record_location_host_contract():
    from retrieval.contracts import CONTRACTS
    c = CONTRACTS["RECORD_LOCATION_HOST"]
    assert c.validate({"businessName": "Abarrotes Lupita", "zoneText": "Pitic"}) == []
    assert c.validate({"businessName": "Gym Fit", "zoneText": "Centro", "businessType": "gimnasio",
                       "contactRole": "dueno", "opensWeekends": True, "spaceType": "interior",
                       "dailyVisitors": "50_200", "hasParking": False, "contactOptIn": True}) == []
    assert c.validate({"businessName": "Gym Fit"})  # zone required
    assert c.validate({"businessName": "x", "zoneText": "y", "businessType": "bar"})
    assert c.validate({"businessName": "x", "zoneText": "y", "contactOptIn": "sí"})
    assert c.validate({"businessName": "x", "zoneText": "y", "companyId": 1})


def test_zone_name_is_optional_text_next_to_the_original():
    from retrieval.contracts import CONTRACTS
    d = CONTRACTS["RECORD_DEMAND_SIGNAL"]
    assert d.validate({"concept": "lockers", "preferredZoneText": "por la Pitic", "preferredZoneName": "Pitic"}) == []
    assert d.validate({"concept": "lockers", "preferredZoneName": "  "})
    h = CONTRACTS["RECORD_LOCATION_HOST"]
    assert h.validate({"businessName": "Abarrotes Lupita", "zoneText": "la Pitic", "zoneName": "Pitic"}) == []
