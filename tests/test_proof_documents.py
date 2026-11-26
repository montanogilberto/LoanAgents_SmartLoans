"""Comprobantes vs INE. Fictional people; does not call Gemini."""
import base64
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import proof_documents_api as api
from agents.id_document.curp import check_digit
from proof_documents.matching import match_address, match_name
from proof_documents.review import monthly_net, review_address, review_payroll

TODAY = date(2026, 11, 24)
CURP = "LOPA900101MSRPRN0"
CURP += str(check_digit(CURP))
OTHER_CURP = "LOPB900101HSRPRN0"
OTHER_CURP += str(check_digit(OTHER_CURP))
INE = {"name": "LÓPEZ PEÑA ANA", "address": "C GORRION OTE 1 B COL NUEVO SOL 83100 HERMOSILLO, SON.", "curp": CURP}
BILL = {"documentType": "INTERNET", "holderName": "ANA LOPEZ PENA", "street": "GORRION ORIENTE", "exteriorNumber": "1",
        "interiorNumber": "B", "neighborhood": "NUEVO SOL", "postalCode": "83100", "issueDate": "",
        "billingMonth": "2026-11", "dueDate": "2026-11-20", "confidence": 0.95, "signsOfEditing": False}
SLIP = {"documentType": "PAYROLL", "workerName": "ANA LÓPEZ PEÑA", "curp": CURP, "rfc": CURP[:10] + "AB1",
        "payDate": "2026-11-12", "grossMXN": 100.0, "deductionsMXN": 40.0, "netMXN": 60.0,
        "payFrequency": "SEMIMONTHLY", "periodStart": "2026-11-01", "periodEnd": "2026-11-15",
        "confidence": 0.95, "fiscalFolio": "", "signsOfEditing": False}


@pytest.mark.parametrize("doc,expected", [
    ("Ana López Peña", "MATCH"), ("LOPEZ PENA ANA", "MATCH"), ("ANA LOPEZ", "PARTIAL"), ("ANA LOPES PEÑA", "MISMATCH"),
    ("LUIS LOPEZ PEÑA", "MISMATCH"), ("MARIA SOTO RUIZ", "MISMATCH"), ("ANA", "MISMATCH"), ("", "MISSING"),
])
def test_name(doc, expected):
    assert match_name(doc, INE["name"]) == expected


def test_name_typo_abbreviation_and_particles():
    assert match_name("GILBERTO SOTO RUIS", "SOTO RUIZ GILBERTO") == "MISMATCH"
    assert match_name("GILVERTO SOTO RUIZ", "SOTO RUIZ GILBERTO") == "PARTIAL"
    assert match_name("MA. ELENA SOTO RUIZ", "SOTO RUIZ MARIA ELENA") == "PARTIAL"
    assert match_name("JOSE DE LA CRUZ SOTO", "CRUZ SOTO JOSE") == "MATCH"


@pytest.mark.parametrize("change,overall", [
    ({}, "MATCH"),
    ({"exteriorNumber": "1-B", "interiorNumber": ""}, "MATCH"),
    ({"interiorNumber": "C"}, "PARTIAL"),
    ({"exteriorNumber": "11"}, "MISMATCH"),
    ({"exteriorNumber": ""}, "PARTIAL"),
    ({"street": "GORRION PONIENTE"}, "MISMATCH"),
    ({"street": "GORRION"}, "PARTIAL"),
    ({"street": "HERMOSILLO"}, "PARTIAL"),
    ({"street": "CD. OGRNIZ ORIENTE"}, "PARTIAL"),
    ({"street": "OLIVO", "exteriorNumber": "45"}, "MISMATCH"),
    ({"street": "OLIVO", "postalCode": "83200"}, "MISMATCH"),
    ({"postalCode": "83101"}, "PARTIAL"),
    ({"postalCode": "83200", "neighborhood": "LAS QUINTAS"}, "MISMATCH"),
    ({"postalCode": ""}, "PARTIAL"),
])
def test_address(change, overall):
    assert match_address({**BILL, **change}, INE["address"])["overall"] == overall


def test_address_numbered_street():
    doc = {**BILL, "street": "5 DE MAYO", "exteriorNumber": "12", "interiorNumber": "", "neighborhood": "CENTRO"}
    assert match_address(doc, "C 5 DE MAYO 12 COL CENTRO 83100 HERMOSILLO")["overall"] == "MATCH"
    doc["street"] = "16 DE MAYO"
    assert match_address(doc, "C 5 DE MAYO 12 COL CENTRO 83100 HERMOSILLO")["overall"] != "MATCH"


def test_address_without_ine_address_is_review():
    r = review_address(BILL, {**INE, "address": ""}, TODAY, 90)
    assert r["checks"]["address"] == "MISSING" and r["verdict"] == "REVIEW"


@pytest.mark.parametrize("change,verdict", [
    ({}, "MATCH"), ({"holderName": "LUIS SOTO RUIZ"}, "MISMATCH"), ({"billingMonth": "2026-06"}, "REVIEW"),
    ({"billingMonth": "2027-01"}, "REVIEW"), ({"billingMonth": "", "dueDate": ""}, "REVIEW"),
    ({"billingMonth": ""}, "MATCH"), ({"confidence": 0.3}, "REVIEW"), ({"signsOfEditing": True}, "REVIEW"),
    ({"documentType": "PAYROLL"}, "WRONG_DOCUMENT"),
])
def test_bill_verdict(change, verdict):
    assert review_address({**BILL, **change}, INE, TODAY, 90)["verdict"] == verdict


@pytest.mark.parametrize("change,verdict", [
    ({}, "MATCH"), ({"curp": OTHER_CURP}, "MISMATCH"), ({"curp": CURP[:-1] + "X"}, "REVIEW"), ({"rfc": ""}, "REVIEW"),
    ({"netMXN": 90.0}, "REVIEW"), ({"payDate": "2026-01-01"}, "REVIEW"), ({"documentType": "OTHER"}, "WRONG_DOCUMENT"),
    ({"workerName": "LUIS SOTO RUIZ"}, "MISMATCH"),
])
def test_payroll_verdict(change, verdict):
    assert review_payroll({**SLIP, **change}, INE, TODAY, 90)["verdict"] == verdict


@pytest.mark.parametrize("frequency,start,end,check", [
    ("WEEKLY", "2026-11-02", "2026-11-08", "MATCH"), ("BIWEEKLY_14", "2026-11-02", "2026-11-15", "MATCH"),
    ("SEMIMONTHLY", "2026-11-16", "2026-11-30", "MATCH"), ("SEMIMONTHLY", "2026-02-16", "2026-02-28", "MATCH"),
    ("MONTHLY", "2026-11-01", "2026-11-30", "MATCH"),
    ("SEMIMONTHLY", "2026-11-02", "2026-11-15", "MISMATCH"),   # "Quinc." printed, but a 14-day period
    ("WEEKLY", "2026-11-01", "2026-11-15", "MISMATCH"),
    ("SEMIMONTHLY", "", "", "PARTIAL"), ("SEMIMONTHLY", "2026-11-15", "2026-11-01", "PARTIAL"),
    ("OTHER", "2026-11-01", "2026-11-15", "MISSING"), ("UNKNOWN", "2026-11-01", "2026-11-15", "MISSING"),
])
def test_payroll_frequency_against_the_period(frequency, start, end, check):
    result = review_payroll({**SLIP, "payFrequency": frequency, "periodStart": start, "periodEnd": end}, INE, TODAY, 90)
    assert result["checks"]["payFrequency"] == check
    assert result["verdict"] == ("MATCH" if check == "MATCH" else "REVIEW")
    assert (result["monthlyNetEstimate"] is not None) == (check == "MATCH")


@pytest.mark.parametrize("net,frequency,monthly", [
    (1000.0, "WEEKLY", 4333.33), (1000.0, "FOURTEEN_DAYS", 2166.66), (1000.0, "SEMI_MONTHLY", 2000.0),
    (1000.0, "MONTHLY", 1000.0), (2503.07, "WEEKLY", 10846.63), (0.0, "WEEKLY", None), (1000.0, "", None),
])
def test_monthly_net_rounds_down(net, frequency, monthly):
    assert monthly_net(net, frequency) == monthly


def test_payroll_names_the_frequency_like_the_income_verification():
    result = review_payroll({**SLIP, "payFrequency": "BIWEEKLY_14", "periodStart": "2026-11-02",
                             "periodEnd": "2026-11-15"}, INE, TODAY, 90)
    assert result["fields"]["payFrequency"] == "FOURTEEN_DAYS" and result["monthlyNetEstimate"] == 130.0


def test_no_estimate_when_totals_do_not_add_up():
    result = review_payroll({**SLIP, "netMXN": 90.0}, INE, TODAY, 90)
    assert result["verdict"] == "REVIEW" and result["monthlyNetEstimate"] is None


def test_payroll_ignores_the_ine_address():
    assert review_payroll(SLIP, {**INE, "address": "OTRA 9"}, TODAY, 90)["verdict"] == "MATCH"


def test_empty_reading_is_unreadable():
    assert review_address({}, INE, TODAY, 90)["verdict"] == "UNREADABLE"
    assert review_payroll({}, INE, TODAY, 90)["verdict"] == "UNREADABLE"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PROOF_DOCUMENTS_ENABLED", "1")
    monkeypatch.setattr(api.settings, "WORKER_KEY", "test-key")
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _post(client, data=b"%PDF-1.4 x", key="test-key"):
    return client.post("/proof-documents/address", headers={"X-Worker-Key": key},
                       json={"documentBase64": base64.b64encode(data).decode(), "ine": INE})


def test_route_fails_closed(client, monkeypatch):
    assert _post(client, key="bad").status_code == 401
    monkeypatch.delenv("PROOF_DOCUMENTS_ENABLED")
    assert _post(client).status_code == 503


def test_route_rejects_other_files(client):
    assert _post(client, b"<html>").status_code == 415


def test_route_returns_the_proposal_and_keeps_nothing(client, monkeypatch):
    async def fake(runner, data, mime):
        assert mime == "application/pdf"
        return dict(BILL)
    monkeypatch.setattr(api, "_read", fake)
    body = _post(client).json()
    assert body["agentName"] == "proof_of_address_agent" and len(body["contentHash"]) == 64
    assert body["checks"]["name"] == "MATCH"


def test_session_is_deleted_even_on_failure(monkeypatch, client):
    class Boom:
        def run_async(self, **_):
            raise RuntimeError("x")
    monkeypatch.setattr(api, "_address_runner", Boom())
    assert _post(client).status_code == 502
    assert not api._sessions.sessions.get(api.APP_NAME, {}) or not any(api._sessions.sessions[api.APP_NAME].values())
