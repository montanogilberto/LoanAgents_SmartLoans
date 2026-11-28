"""smartScore capacity rule (PRD §8.4, AC7, AC9, AC15). Does not call Gemini."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import smart_score_api as api
from smart_score.rules import compute, per_period
from decimal import Decimal

BASE = {"policyVersion": "v1", "capRatio": 0.30, "verifiedMonthlyIncome": 20000.0,
        "declaredMonthlyIncome": None, "essentialExpensesMonthly": 9000.0, "debtPaymentsMonthly": 3000.0,
        "requestedInstallment": None, "loanFrequency": None}


def results(out):
    return {f["code"]: f["result"] for f in out["factors"]}


def test_spec_example():
    out = compute(BASE)   # 20,000 − 9,000 − 3,000 = 8,000 × 30 % = 2,400
    assert out["capacity"]["availableMonthly"] == 8000.0
    assert out["capacity"]["maxInstallmentMonthly"] == 2400.0
    assert out["level"] == "OK"
    assert out["capacity"]["maxInstallmentByFrequency"] == {
        "WEEKLY": 553.84, "FOURTEEN_DAYS": 1107.69, "SEMI_MONTHLY": 1200.0, "MONTHLY": 2400.0}


@pytest.mark.parametrize("frequency", ["WEEKLY", "FOURTEEN_DAYS", "SEMI_MONTHLY", "MONTHLY"])
def test_exact_limit_and_one_cent_over(frequency):
    limit = compute(BASE)["capacity"]["maxInstallmentByFrequency"][frequency]
    at = compute({**BASE, "requestedInstallment": limit, "loanFrequency": frequency})
    over = compute({**BASE, "requestedInstallment": round(limit + 0.01, 2), "loanFrequency": frequency})
    assert results(at)["INSTALLMENT"] == "PASS" and at["level"] == "OK"
    assert results(over)["INSTALLMENT"] == "FAIL" and over["level"] == "STRONG_ALERT"


def test_rounds_down_to_cents():
    assert per_period(Decimal("100.00"), "WEEKLY") == Decimal("23.07")   # 23.0769…
    out = compute({**BASE, "verifiedMonthlyIncome": 12000.01, "essentialExpensesMonthly": 0, "debtPaymentsMonthly": 0,
                   "capRatio": 0.333})
    assert out["capacity"]["maxInstallmentMonthly"] == 3996.0   # 3,996.00333 → down


def test_no_margin_is_strong_alert_not_rejection():
    out = compute({**BASE, "essentialExpensesMonthly": 15000.0, "debtPaymentsMonthly": 6000.0})
    assert results(out)["CAPACITY"] == "FAIL"
    assert out["capacity"]["availableMonthly"] == -1000.0
    assert out["capacity"]["maxInstallmentMonthly"] == 0.0
    assert out["level"] == "STRONG_ALERT"


@pytest.mark.parametrize("missing", ["essentialExpensesMonthly", "debtPaymentsMonthly", "verifiedMonthlyIncome"])
def test_missing_data_is_unknown_never_zero(missing):
    out = compute({**BASE, missing: None, "requestedInstallment": 100.0, "loanFrequency": "MONTHLY"})
    assert out["capacity"] is None
    assert results(out)["CAPACITY"] == "UNKNOWN"
    assert results(out)["INSTALLMENT"] == "UNKNOWN"
    assert out["level"] == "INCOMPLETE"


def test_declared_income_only_asks_for_review():
    out = compute({**BASE, "verifiedMonthlyIncome": None, "declaredMonthlyIncome": 45824.0})
    assert results(out)["INCOME"] == "REVIEW"
    assert out["capacity"] is None
    assert out["level"] == "REVIEW"


def test_hash_is_stable_and_input_bound():
    assert compute(BASE)["inputsHash"] == compute(dict(BASE))["inputsHash"]
    assert compute(BASE)["inputsHash"] != compute({**BASE, "debtPaymentsMonthly": 3000.01})["inputsHash"]


# --- route ---

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SMART_SCORE_ENABLED", "1")
    monkeypatch.setattr(api.settings, "WORKER_KEY", "k")

    async def fake_explain(result, req):
        return "texto"
    monkeypatch.setattr(api, "explain", fake_explain)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


BODY = {k: v for k, v in BASE.items() if v is not None}


def test_route_requires_key(client):
    assert client.post("/smart-score/capacity", json=BODY).status_code == 401
    assert client.post("/smart-score/capacity", json=BODY, headers={"X-Worker-Key": "x"}).status_code == 401


def test_route_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("SMART_SCORE_ENABLED")
    assert client.post("/smart-score/capacity", json=BODY, headers={"X-Worker-Key": "k"}).status_code == 503


def test_route_answers(client):
    r = client.post("/smart-score/capacity", json=BODY, headers={"X-Worker-Key": "k"})
    assert r.status_code == 200
    body = r.json()
    assert body["level"] == "OK" and body["mode"] == "SHADOW" and body["policyVersion"] == "v1"
    assert body["capacity"]["maxInstallmentMonthly"] == 2400.0 and body["explanation"] == "texto"


@pytest.mark.parametrize("change", [
    {"policyVersion": ""}, {"capRatio": 0}, {"capRatio": 1.5}, {"debtPaymentsMonthly": -1},
    {"requestedInstallment": 100},   # without loanFrequency
])
def test_route_rejects_bad_input(client, change):
    body = {**BODY, **change}
    assert client.post("/smart-score/capacity", json=body, headers={"X-Worker-Key": "k"}).status_code == 422


def test_route_without_version_is_rejected(client):
    body = {k: v for k, v in BODY.items() if k != "policyVersion"}
    assert client.post("/smart-score/capacity", json=body, headers={"X-Worker-Key": "k"}).status_code == 422
