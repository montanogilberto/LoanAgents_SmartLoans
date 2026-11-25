"""Live check of collections_whatsapp against Gemini: sample loan, no backend, sends nothing.

  python scripts/collections_whatsapp_sim.py --runs 3
"""
import argparse
import os
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["COLLECTIONS_ENABLED"] = "1"
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import collections_api as api  # noqa: E402
from config import settings  # noqa: E402
from loan_collections.situation import hermosillo_now, next_days  # noqa: E402

KEY = settings.WORKER_KEY = "sim-only"
TODAY = hermosillo_now().date()
OVERDUE = {"status": "overdue", "asOf": TODAY.isoformat(), "overdueCount": 1, "overdueAmount": 1500.0,
           "oldestDueDate": (TODAY - timedelta(days=12)).isoformat(), "daysLate": 12, "nextDueDate": None,
           "nextDueAmount": None, "daysToNextDue": None, "pendingInstallments": 1}
HOLD = {"status": "hold", "reason": "payment_under_review"}
FRIDAY = next(d["date"] for d in next_days(TODAY) if d["weekday"] == "viernes")


def cap(name):
    return lambda out: (out["pendingAction"] or {}).get("capability") == name


def promise(amount):
    return lambda o: cap("PROMISE_TO_PAY")(o) and o["pendingAction"]["fields"].get("amount") == amount \
        and o["pendingAction"]["fields"].get("date") == FRIDAY


CASES = {   # name: (situation, messages, check on the last answer)
    "situation": (OVERDUE, ["¿cuánto debo?"], lambda o: "1,500" in o["reply"] or "1500" in o["reply"]),
    "promise": (OVERDUE, ["hola", "pago todo el viernes"], promise(1500)),
    "partial": (OVERDUE, ["puedo pagar 500 el viernes"], promise(500)),
    "already_paid": (OVERDUE, ["ya pagué ayer"], cap("REQUEST_HUMAN")),
    "wrong_number": (OVERDUE, ["no soy Ana, número equivocado"],
                     lambda o: cap("REQUEST_HUMAN")(o) and "préstamo" not in o["reply"] and "1,500" not in o["reply"]),
    "stop": (OVERDUE, ["ya no me escriban"], cap("REQUEST_HUMAN")),
    "discount": (OVERDUE, ["hazme un descuento y pago hoy"], cap("REQUEST_HUMAN")),
    "hold": (HOLD, ["¿cuánto debo?"], lambda o: cap("REQUEST_HUMAN")(o) and not any(c.isdigit() for c in o["reply"])),
}

ap = argparse.ArgumentParser()
ap.add_argument("--runs", type=int, default=3)
args = ap.parse_args()
app = FastAPI()
app.include_router(api.router)
http = TestClient(app)
slowest, total = 0.0, 0
for name, (situation, messages, check) in CASES.items():
    api.fetch_situation = lambda *a, s=situation: dict(s)
    hits = 0
    for _ in range(args.runs):
        phone = uuid.uuid4().hex[:12]
        for m in messages:
            t = time.monotonic()
            r = http.post("/collections/whatsapp", headers={"X-Worker-Key": KEY}, json={
                "companyId": 1008, "clientId": 900001, "loanId": 900001, "companyName": "Préstamos Prueba",
                "firstName": "Ana", "phone": phone, "message": m})
            slowest = max(slowest, time.monotonic() - t)
            r.raise_for_status()
            out = r.json()
        ok = check(out)
        hits += ok
        print(("ok  " if ok else "FAIL"), name, "|", out["reply"].replace("\n", " / "), "|", out["pendingAction"])
    total += hits
    print(f"== {name}: {hits}/{args.runs}")
print(f"TOTAL {total}/{len(CASES) * args.runs}  slowest turn {slowest:.1f}s")
