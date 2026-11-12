"""WhatsApp registration sub-agent: wiring, trusted lookup and the REGISTER_CLIENT contract."""
from types import SimpleNamespace
from unittest import mock

from agents.whatsapp_registration import whatsapp_registration_agent
from agents.whatsapp_reservations import whatsapp_reservations_agent
from retrieval.contracts import CONTRACTS
from tools import backend_api
from tools.pending_actions import propose_action


def _ctx(**state):
    return SimpleNamespace(state=dict(state))


def test_registration_is_a_sub_agent_sharing_the_reply_key():
    assert whatsapp_reservations_agent.sub_agents == [whatsapp_registration_agent]
    assert whatsapp_registration_agent.parent_agent is whatsapp_reservations_agent
    assert whatsapp_registration_agent.output_key == whatsapp_reservations_agent.output_key
    assert whatsapp_registration_agent.model == "gemini-2.5-flash"


def test_registration_tools_take_no_phone_or_company():
    names = {t.name for t in whatsapp_registration_agent.tools}
    assert names == {"get_my_registration", "propose_action"}
    import inspect
    assert list(inspect.signature(backend_api.get_my_registration).parameters) == ["tool_context"]


def test_contract_requires_name_and_last_name():
    contract = CONTRACTS["REGISTER_CLIENT"]
    assert contract.validate({"fullName": "Ana López Ruiz"}) == []
    assert contract.validate({"fullName": "Ana"})
    assert contract.validate({})
    assert contract.validate({"fullName": "Ana López", "companyId": 2})


def test_propose_register_client_stores_the_proposal():
    ctx = _ctx()
    assert propose_action("REGISTER_CLIENT", {"fullName": "Ana López"}, "¿Confirmas?", ctx) == {"proposed": True}
    assert ctx.state["pending_action"]["capability"] == "REGISTER_CLIENT"
    assert propose_action("REGISTER_CLIENT", {"fullName": "Ana"}, "¿Confirmas?", _ctx())["proposed"] is False


def test_lookup_uses_the_session_phone_and_company():
    resp = mock.MagicMock()
    resp.json.return_value = {"result": [{"status": "found", "firstName": "Ana", "balance": 40, "clientId": 7}]}
    with mock.patch.object(backend_api, "WORKER_KEY", "k"), \
            mock.patch.object(backend_api.httpx, "post", return_value=resp) as post:
        out = backend_api.get_my_registration(_ctx(wa_company_id=1, wa_customer_phone="+5216621234567"))
    assert out == {"status": "found", "firstName": "Ana"}  # nothing else leaks
    assert post.call_args.kwargs["json"] == {"customerRewards": [{"companyId": 1, "phone": "+5216621234567"}]}


def test_lookup_without_worker_key_or_on_error_is_unavailable():
    assert backend_api.get_my_registration(_ctx(wa_company_id=1, wa_customer_phone="1"))["status"] in ("unavailable",) \
        or backend_api.WORKER_KEY
    with mock.patch.object(backend_api, "WORKER_KEY", "k"), \
            mock.patch.object(backend_api.httpx, "post", side_effect=RuntimeError("down")):
        assert backend_api.get_my_registration(_ctx(wa_company_id=1, wa_customer_phone="1")) == {"status": "unavailable"}
    resp = mock.MagicMock()
    resp.json.return_value = {"result": [{"status": "weird"}]}
    with mock.patch.object(backend_api, "WORKER_KEY", "k"), \
            mock.patch.object(backend_api.httpx, "post", return_value=resp):
        assert backend_api.get_my_registration(_ctx(wa_company_id=1, wa_customer_phone="1")) == {"status": "unavailable"}
