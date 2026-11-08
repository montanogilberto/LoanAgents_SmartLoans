"""
Places ONE real test call through Twilio to YOUR OWN phone, connected to the
voice secretary. Dev only — production calls will be placed by smartloans_backend.

Twilio credentials are NOT kept in this repo: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN
are read from smartloans_backend's .env (the backend owns Twilio; in production it is
the one that places the calls). Override the path with BACKEND_ENV_FILE.
Needs in this repo's .env:
  TWILIO_VOICE_FROM                       caller ID: a Twilio number or a Verified Caller ID (E.164)
  VOICE_CALL_SECRET                       same value the running app has
  VOICE_TEST_ALLOWED_TO                   your own number (E.164) — the only one this script will dial
And a running app reachable over wss:// (e.g. `uvicorn main:app --port 8080` + `ngrok http 8080`,
or the deployed Web App with WebSockets enabled).

  python scripts/voice_test_call.py --client-id 3372 --host abcd.trycloudflare.com --service "su ropa"

The number dialed is the client's own cellphone from the POS (read-only lookup) —
never passed separately, so the call and the client it talks about can't disagree.
It must equal VOICE_TEST_ALLOWED_TO; the walk-in client (1) and the caller ID itself
are refused.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from dotenv import dotenv_values, load_dotenv  # noqa: E402

from tools.backend_api import get_one_client  # noqa: E402
from voice.call_token import WALK_IN_CLIENT_ID, sign_call_token  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
BACKEND_ENV_FILE = os.environ.get(
    "BACKEND_ENV_FILE", str(Path.home() / "PycharmProjects/pythonProject/smartloans_backend/.env"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", type=int, required=True, help="POS clientId; its cellphone is dialed")
    ap.add_argument("--host", required=True, help="public host of the running app, no scheme")
    ap.add_argument("--company-id", type=int, default=1)
    ap.add_argument("--service", default=None, help='what is ready, e.g. "su ropa"')
    ap.add_argument("--total", type=float, default=None, help="amount to collect; omit if unknown")
    args = ap.parse_args()

    if args.client_id == WALK_IN_CLIENT_ID:
        sys.exit("Refusing: client 1 is the walk-in placeholder.")
    client = get_one_client(args.client_id)
    if client.get("companyId") != args.company_id:
        sys.exit(f"Refusing: client {args.client_id} is not in company {args.company_id}.")
    digits = "".join(ch for ch in str(client.get("cellphone") or "") if ch.isdigit())
    to = "+" + digits if len(digits) == 12 else ("+52" + digits if len(digits) == 10 else "")
    allowed = os.environ.get("VOICE_TEST_ALLOWED_TO", "")
    if not to or to != allowed:
        sys.exit("Refusing: the client's cellphone must equal VOICE_TEST_ALLOWED_TO (your own number).")
    backend = dotenv_values(BACKEND_ENV_FILE)
    sid, auth = backend.get("TWILIO_ACCOUNT_SID") or "", backend.get("TWILIO_AUTH_TOKEN") or ""
    from_ = os.environ.get("TWILIO_VOICE_FROM", "")
    if not (sid and auth):
        sys.exit(f"No TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN in {BACKEND_ENV_FILE}.")
    if not from_:
        sys.exit("Missing TWILIO_VOICE_FROM in this repo's .env.")
    if from_ == to:
        sys.exit("Refusing: the client's phone is the caller ID itself.")

    token = sign_call_token({
        "companyId": args.company_id, "branchId": None, "clientId": args.client_id, "orderId": None,
        "companyName": "Lavandería", "customerName": client.get("first_name"),
        "order": {"service": args.service, "total": args.total},
    }, os.environ.get("VOICE_CALL_SECRET", ""))
    twiml = (f'<Response><Connect><Stream url={quoteattr(f"wss://{args.host}/voice/twilio-stream")}>'
             f'<Parameter name="token" value={quoteattr(token)}/></Stream></Connect></Response>')

    resp = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                      auth=(sid, auth), data={"To": to, "From": from_, "Twiml": twiml}, timeout=15)
    if resp.status_code >= 300:
        sys.exit(f"Twilio refused the call: {resp.status_code} {resp.text[:300]}")
    # Queued = Twilio accepted it, not that it was answered.
    print("Call queued:", resp.json().get("sid"), resp.json().get("status"))


if __name__ == "__main__":
    main()
