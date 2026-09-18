"""
Calls into smartloans_backend's public REST API — this repo never touches
the database or imports backend Python modules directly, per the
independent-repo/API-only architecture.
"""
from datetime import datetime, timedelta, timezone

import httpx

from config.settings import SMARTLOANS_BACKEND_URL

_TIMEOUT = 15.0

# UTC-7, no DST -- same convention as POSVending's
# src/utils/format.ts::toHermosilloDate, kept in sync deliberately so
# "today" means the same calendar day on both sides of the chat.
_HERMOSILLO_OFFSET = timedelta(hours=7)


def _to_hermosillo(iso: str) -> datetime:
    s = iso if ("+" in iso[10:] or iso.endswith("Z")) else iso + "Z"
    return datetime.fromisoformat(s.replace("Z", "+00:00")) - _HERMOSILLO_OFFSET


def _post(path: str, body: dict) -> dict:
    resp = httpx.post(f"{SMARTLOANS_BACKEND_URL}{path}", json=body, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _get(path: str) -> dict:
    resp = httpx.get(f"{SMARTLOANS_BACKEND_URL}{path}", timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _cash_register_output(company_id: int, action: int):
    """Shared helper for the /cashRegister action-envelope endpoint — the
    real payload lives in result[0].output_json, per sp_cashRegister's
    generic {value,msg,error,output_json} row shape."""
    result = _post("/cashRegister", {"register": [{"action": action, "companyId": company_id}]})
    rows = result.get("result", []) if isinstance(result, dict) else []
    return rows[0].get("output_json") if rows else None


def get_conversation(conversation_id: int) -> dict:
    """Fetches a loanChat conversation (borrower/lender ids, status, agreed terms).

    Args:
        conversation_id: The loanChat conversationId.

    Returns:
        The conversation record, or {} if not found.
    """
    result = _post("/loanChat", {"chat": [{"action": "get_conversation", "conversationId": conversation_id}]})
    return result if isinstance(result, dict) and "error" not in result else {}


def get_client_loans(client_id: int, company_id: int) -> list[dict]:
    """Fetches the loans this client participates in — as BORROWER (loans they
    received) or as LENDER (loans they funded; loans.clientId is always the
    borrower, so the lender link is the 'Prestamista clientId=N' notes tag).

    Args:
        client_id: The clientId (borrower or lender).
        company_id: The company scoping the loans.

    Returns:
        List of loan records, each with a "myRole" field: borrower | lender.
    """
    result = _post("/all_loans", {"loans": [{"companyId": company_id}]})
    loans = result.get("loans", []) if isinstance(result, dict) else []
    mine = []
    for l in loans:
        if l.get("clientId") == client_id:
            mine.append({**l, "myRole": "borrower"})
        elif f"Prestamista clientId={client_id}" in (l.get("notes") or ""):
            mine.append({**l, "myRole": "lender"})
    return mine


def get_client_dashboard(client_id: int, company_id: int) -> dict:
    """Fetches this borrower's dashboard summary (available credit, active balance, next payment).

    Args:
        client_id: The borrower's clientId.
        company_id: The company scoping the dashboard.

    Returns:
        The dashboard record, or {} if none exists.
    """
    result = _post("/all_clientDashboards", {"clientDashboards": [{"companyId": company_id, "clientId": client_id}]})
    dashboards = result.get("clientDashboards", []) if isinstance(result, dict) else []
    return dashboards[0] if dashboards else {}


def get_credit_score(client_id: int, company_id: int) -> dict:
    """Fetches this borrower's credit score and label.

    Args:
        client_id: The borrower's clientId.
        company_id: The company scoping the score.

    Returns:
        {"score": int, "label": str} or {} if unavailable.
    """
    result = _post("/credit-score", {"clientId": client_id, "companyId": company_id})
    return result.get("creditScore", {}) if isinstance(result, dict) else {}


def get_wallet_balance(client_id: int, company_id: int) -> dict:
    """Fetches this client's SPEI wallet balance (the real money they can lend
    or use to pay cuotas — distinct from published capital, which is only an
    announcement).

    Args:
        client_id: The client's clientId (borrower or lender).
        company_id: The company scoping the wallet.

    Returns:
        {"availableBalance": float, "reservedBalance": float} or {}.
    """
    result = _post("/ledger/balance", {"companyId": company_id, "clientId": client_id})
    return result if isinstance(result, dict) and "error" not in result else {}


def get_wallet_movements(client_id: int, company_id: int) -> list[dict]:
    """Fetches this client's wallet movement statement (deposits, loan funding,
    repayments received, withdrawals), newest first.

    Args:
        client_id: The client's clientId.
        company_id: The company scoping the ledger.

    Returns:
        List of ledger entries (entryType, direction, amountMXN, balanceAfter, created_At).
    """
    result = _post("/all_walletTransactions", {"walletTransactions": [{"companyId": company_id, "clientId": client_id}]})
    return result.get("walletTransactions", []) if isinstance(result, dict) else []


def get_bank_accounts(client_id: int, company_id: int) -> list[dict]:
    """Fetches this client's linked CLABE bank accounts. The API is already
    masked — it only ever exposes clabeLast4, never the full CLABE.

    Args:
        client_id: The client's clientId.
        company_id: The company scoping the accounts.

    Returns:
        List of {"bankName": str, "clabeLast4": str, "isVerified": bool, "isDefault": bool}.
    """
    result = _post("/all_bankAccounts", {"bankAccounts": [{"companyId": company_id, "clientId": client_id}]})
    accounts = result.get("bankAccounts", []) if isinstance(result, dict) else []
    return [{
        "bankName": a.get("bankName"),
        "clabeLast4": a.get("clabeLast4"),
        "isVerified": bool(a.get("isVerified")),
        "isDefault": bool(a.get("isDefault")),
    } for a in accounts]


def get_installment_schedule(loan_id: int, company_id: int) -> list[dict]:
    """Fetches a loan's cuota (installment) schedule: due dates, amounts,
    principal/interest split, and paid/pending status.

    Args:
        loan_id: The loanId whose schedule to read.
        company_id: The company scoping the loan.

    Returns:
        List of installment records.
    """
    result = _post("/automated-payments/schedule", {"loanId": loan_id, "companyId": company_id})
    return result.get("installments", []) if isinstance(result, dict) else []


def get_my_offers(client_id: int, company_id: int) -> list[dict]:
    """Fetches the ACTIVE capital offers this lender has published in the P2P
    marketplace (announced capital — distinct from wallet money).

    Args:
        client_id: The lender's clientId.
        company_id: The company scoping the offers.

    Returns:
        List of this lender's active offers (availableCapital, minRate,
        maxRate, term range).
    """
    result = _post("/all_loanOffers", {"loanOffers": [{"companyId": company_id, "isActive": True}]})
    offers = result.get("loanOffers", []) if isinstance(result, dict) else []
    return [o for o in offers if o.get("lenderId") == client_id]


def get_client_contracts(client_id: int, company_id: int) -> list[dict]:
    """Fetches every digital contract where this client is the borrower or the
    lender (Contrato de Crédito P2P + Pagaré metadata).

    Args:
        client_id: The client's clientId.
        company_id: The company scoping the contracts.

    Returns:
        List of contract records (list_contracts returns a PLAIN JSON array,
        matching the frontend's digitalContractsApi.listContractsForClient).
    """
    result = _post("/digitalContracts", {"contract": [{"action": "list_contracts", "companyId": company_id, "clientId": client_id}]})
    return result if isinstance(result, list) else []


def create_contract(
    company_id: int,
    loan_id: int,
    conversation_id: int,
    borrower_client_id: int,
    lender_client_id: int,
    principal_amount: float,
    interest_rate: float,
    term_months: int,
    contract_summary: str,
) -> dict:
    """Creates a digital loan contract once borrower and lender have agreed
    on terms. Wraps smartloans_backend's existing digitalContracts API — the
    borrower/lender still sign it themselves afterward (sign_contract action,
    not exposed here since this agent never signs on anyone's behalf).

    Args:
        company_id: The company scoping the loan.
        loan_id: The loanId the contract is for.
        conversation_id: The loanChat conversationId the agreement came from.
        borrower_client_id: The borrower's clientId.
        lender_client_id: The lender's clientId.
        principal_amount: The agreed loan amount.
        interest_rate: The agreed annual interest rate (percent).
        term_months: The agreed repayment term in months.
        contract_summary: A short plain-text summary of the agreed terms,
            stored as the contract's notes.

    Returns:
        The created contract record, or {"error": ...} on failure.
    """
    return _post("/digitalContracts", {
        "contract": [{
            "action": "create_contract",
            "companyId": company_id,
            "loanId": loan_id,
            "conversationId": conversation_id,
            "borrowerClientId": borrower_client_id,
            "lenderClientId": lender_client_id,
            "principalAmount": principal_amount,
            "interestRate": interest_rate,
            "termMonths": term_months,
            "notes": contract_summary,
        }]
    })


def get_cash_register_daily_summary(company_id: int) -> dict:
    """Fetches a company's cash register daily summary (action 7): opening
    cash, sales, deposits, withdrawals, and expected vs. physical cash
    counted at close.

    Args:
        company_id: The company scoping the register.

    Returns:
        {"openingCash", "sales", "deposits", "withdrawals", "expectedCash",
        "physicalCash", "difference"} or {} if no session is open/found.
    """
    output = _cash_register_output(company_id, action=7)
    return output if isinstance(output, dict) else {}


def list_cash_register_movements(company_id: int) -> list[dict]:
    """Fetches a company's cash register movements (cash in/out) for the
    currently open session (action 5).

    Args:
        company_id: The company scoping the register.

    Returns:
        List of movement records (movementType, amount, notes, createdAt).
    """
    output = _cash_register_output(company_id, action=5)
    return output if isinstance(output, list) else []


def get_monthly_income(company_id: int) -> dict:
    """Fetches this calendar month's income and aggregates it.

    /monthly_income does NOT return a pre-aggregated summary row — it
    returns every individual transaction for the month (verified
    2026-09-15: 65 rows for one real company). A previous version of this
    function assumed otherwise and returned income[0] -- one arbitrary
    transaction mislabeled as "the" figure, which is why the income
    support agent was reporting "no data" even when real income existed:
    it correctly distrusted a single transaction row as a monthly total
    and refused to guess. Aggregating client-side here, the same pattern
    get_expense_total already uses for expenses, fixes that.

    Args:
        company_id: The company to scope to.

    Returns:
        {"companyId", "monthlyTotal", "monthlyCount", "todayTotal",
        "todayCount", "yesterdayTotal", "yesterdayCount",
        "yesterdayInPreviousMonth"} -- monthly figures cover every returned
        row; today's/yesterday's are the subset whose paymentDate falls on
        that date in Hermosillo local time (UTC-7, no DST) -- the same
        "Ventas Hoy" the POS dashboard itself computes. If yesterday was the
        last day of the PREVIOUS calendar month, this endpoint has no data
        for it (it only ever returns the current month) --
        yesterdayInPreviousMonth is True and yesterdayTotal/yesterdayCount
        are None in that one case, rather than a misleading 0.
    """
    result = _post("/monthly_income", {"income": [{"companyId": company_id}]})
    rows = result.get("income", []) if isinstance(result, dict) else []
    today = (datetime.now(timezone.utc) - _HERMOSILLO_OFFSET).date()
    yesterday = today - timedelta(days=1)
    yesterday_in_previous_month = yesterday.month != today.month

    if not rows:
        return {
            "companyId": company_id, "monthlyTotal": 0.0, "monthlyCount": 0,
            "todayTotal": 0.0, "todayCount": 0,
            "yesterdayTotal": None if yesterday_in_previous_month else 0.0,
            "yesterdayCount": None if yesterday_in_previous_month else 0,
            "yesterdayInPreviousMonth": yesterday_in_previous_month,
        }

    def _net(row: dict) -> float:
        return float(row.get("total") or 0) - float(row.get("discountAmount") or 0)

    today_rows = [r for r in rows if _to_hermosillo(r["paymentDate"]).date() == today]
    yesterday_rows = [r for r in rows if _to_hermosillo(r["paymentDate"]).date() == yesterday]

    return {
        "companyId": company_id,
        "monthlyTotal": round(sum(_net(r) for r in rows), 2),
        "monthlyCount": len(rows),
        "todayTotal": round(sum(_net(r) for r in today_rows), 2),
        "todayCount": len(today_rows),
        "yesterdayTotal": None if yesterday_in_previous_month else round(sum(_net(r) for r in yesterday_rows), 2),
        "yesterdayCount": None if yesterday_in_previous_month else len(yesterday_rows),
        "yesterdayInPreviousMonth": yesterday_in_previous_month,
    }


def get_expense_total(company_id: int, from_date: str | None = None, to_date: str | None = None) -> dict:
    """Sums this company's expenses over an optional date range. No backend
    aggregate endpoint exists for expenses (unlike income's /monthly_income),
    so this fetches /all_expense and sums client-side — same
    fetch-then-filter pattern as get_recent_expenses, just totaled instead
    of listed.

    Args:
        company_id: The company to filter to.
        from_date: Optional 'YYYY-MM-DD' lower bound (inclusive) on paymentDate.
        to_date: Optional 'YYYY-MM-DD' upper bound (inclusive) on paymentDate.

    Returns:
        {"total": float, "count": int} over the matching rows.
    """
    result = _get("/all_expense")
    expenses = result.get("expenses", []) if isinstance(result, dict) else []
    mine = [e for e in expenses if e.get("companyId") == company_id]
    if from_date:
        mine = [e for e in mine if (e.get("paymentDate") or "") >= from_date]
    if to_date:
        mine = [e for e in mine if (e.get("paymentDate") or "") <= to_date]
    total = sum(float(e.get("total") or 0) for e in mine)
    return {"total": total, "count": len(mine)}


def get_trial_balance(company_id: int, to_date: str | None = None) -> dict:
    """Fetches the real Balanza de Comprobación (trial balance) — the
    authoritative accounting result, built from journalEntries/
    journalEntryLines, not a client-side approximation. Income and expense
    inserts already auto-post here (modules/journalEntries.py in the
    backend), so this reflects real posted transactions.

    Args:
        company_id: The company to scope to.
        to_date: Optional 'YYYY-MM-DD' cutoff — entries posted on or before
            this date. Omit for "as of now".

    Returns:
        {"accounts": [...], "totalDebit": float, "totalCredit": float,
        "balanced": bool} — "balanced" should always be true; false would
        mean a real data-integrity problem worth surfacing, not hiding.
    """
    body: dict = {"companyId": company_id}
    if to_date:
        body["toDate"] = to_date
    result = _post("/journalEntries/trial-balance", {"journalEntries": [body]})
    return result if isinstance(result, dict) else {}


def get_recent_expenses(company_id: int, limit: int = 20) -> list[dict]:
    """Fetches this company's most recent expenses. sp_expense_all returns
    every company's expenses unscoped, so filtering by companyId happens
    here, client-side.

    Args:
        company_id: The company to filter to.
        limit: Max number of most-recent expenses to return.

    Returns:
        List of expense records (total, paymentMethod, paymentDate, ...),
        most recent first.
    """
    result = _get("/all_expense")
    expenses = result.get("expenses", []) if isinstance(result, dict) else []
    mine = [e for e in expenses if e.get("companyId") == company_id]
    mine.sort(key=lambda e: e.get("paymentDate") or "", reverse=True)
    return mine[:limit]


def get_client_follow_ups(client_id: int, company_id: int) -> list[dict]:
    """Fetches this client's follow-up/collections history.

    Args:
        client_id: The clientId to filter to.
        company_id: The company scoping the follow-ups.

    Returns:
        List of follow-up records (riskStatus, reason, note, dueDate, ...),
        or [] if none exist.
    """
    result = _post("/all_clientFollowUps", {"clientFollowUps": [{"companyId": company_id, "clientId": client_id}]})
    return result.get("clientFollowUps", []) if isinstance(result, dict) else []


def list_clients(company_id: int, limit: int = 20, name_contains: str | None = None) -> list[dict]:
    """Fetches this company's clients. sp_clients_all returns every
    company's clients unscoped (no companyId parameter), so filtering by
    companyId happens here, client-side — same fetch-then-filter pattern
    already used by get_recent_expenses/get_expense_total for sp_expense_all.
    This function is the trust boundary: only the filtered subset for
    company_id is ever returned to the caller (the agent, then the chat
    reply) — the unscoped full list never leaves this function.

    Args:
        company_id: The company to filter to.
        limit: Max number of most-recently-created clients to return.
        name_contains: Optional case-insensitive substring filter on
            "first_name last_name" (for "busca un cliente llamado ..."
            style questions). Omit to just list recent clients.

    Returns:
        List of client records (clientId, first_name, last_name, cellphone,
        email, clientType, created_At), most recently created first.
    """
    result = _get("/all_clients")
    clients = result.get("clients", []) if isinstance(result, dict) else []
    mine = [c for c in clients if c.get("companyId") == company_id]
    if name_contains:
        needle = name_contains.strip().lower()
        mine = [
            c for c in mine
            if needle in f"{c.get('first_name', '')} {c.get('last_name', '')}".strip().lower()
        ]
    mine.sort(key=lambda c: c.get("created_At") or "", reverse=True)
    return mine[:limit]


def get_reward_balance(company_id: int, client_id: int) -> dict:
    """Fetches a client's real loyalty-points balance via sp_rewards'
    get_balance action (the live rewardPoints table — see
    smartloans_backend/modules/rewards.py). First hop in the GMO
    relationship graph's client -> reward_balance edge (retrieval/graph.py).

    Args:
        company_id: The company scoping the balance.
        client_id: The client to look up.

    Returns:
        {"balance": int, "lifetimeEarned": int, "lifetimeRedeemed": int,
        "lastActivity": str|None}, or zeros if the client has no wallet row yet.
    """
    result = _post("/rewards", {"rewards": [
        {"action": "get_balance", "companyId": company_id, "clientId": client_id}
    ]})
    return result if isinstance(result, dict) else {}


def get_reward_transactions(company_id: int, client_id: int, limit: int = 20) -> list[dict]:
    """Fetches a client's real loyalty-points ledger (earn/redeem history)
    via sp_rewards' list_transactions action. Second hop in the GMO
    relationship graph's client -> reward_transactions edge
    (retrieval/graph.py) — each row's referenceId is the incomeId of the
    sale that earned it (only populated for transactions created after the
    2026-09-15 fix wiring modules.rewards.earn_points_for_income into
    income_sp — older/seed transactions may have a non-numeric or missing
    referenceId, meaning no sale is linked).

    Args:
        company_id: The company scoping the ledger.
        client_id: The client to look up.
        limit: Max number of most-recent transactions to return (the SP
            itself caps at 50; this trims further client-side).

    Returns:
        List of {txId, txType, points, balanceAfter, referenceId,
        description, created_At}, most recent first.
    """
    result = _post("/rewards", {"rewards": [
        {"action": "list_transactions", "companyId": company_id, "clientId": client_id}
    ]})
    txns = result if isinstance(result, list) else []
    return txns[:limit]


def resolve_income_receipt(income_id: int) -> dict:
    """Resolves ONE income/sale to its full receipt: the income header,
    the client who bought it, the cashier, every product line (with
    options), computed totals, and ticket/printing metadata — all in one
    already-existing backend call (sp_tickets_one does this exact join
    server-side, see smartloans_backend/sql — verified live 2026-09-15).
    This is the graph edge reward_transaction -> income in
    retrieval/graph.py: given a referenceId from a reward transaction,
    this turns it into the real sale that earned/spent those points.

    Args:
        income_id: The incomeId to resolve (e.g. from a reward
            transaction's referenceId, once converted to int).

    Returns:
        {"incomeId", "companyId", "paymentDate", "paymentMethod",
        "client": {...}, "user": {...}, "products": [...],
        "totals": {...}, "ticketMeta": {...}}, or {} if not found /
        referenceId didn't point to a real income row.
    """
    result = _post("/one_tickets", {"tickets": [{"income": income_id}]})
    tickets = result.get("tickets", []) if isinstance(result, dict) else []
    return tickets[0] if tickets else {}


def get_one_client(client_id: int) -> dict:
    """Fetches one client's registration record (POS "Clientes" wizard data —
    same entity as a SmartLoans borrower, per this app's shared client table).
    Does not take companyId: sp_clients_one's real contract only filters by
    clientId, not scoped further.

    Args:
        client_id: The clientId to look up.

    Returns:
        The client record (first_name, last_name, cellphone, email,
        clientType, qrBlobUrl, created_At, ...), or {} if not found.
    """
    result = _post("/one_clients", {"clients": [{"clientId": client_id}]})
    clients = result.get("clients", []) if isinstance(result, dict) else []
    return clients[0] if clients else {}


def list_open_orders() -> list[dict]:
    """Fetches today's orders with their current tracking status.
    sp_orders_list is not company-scoped (it has no companyId column) and
    only ever returns today's orders — a pre-existing backend limitation,
    not something this tool works around.

    Returns:
        List of order records (orderId, orderNumber, tableNumber, total,
        orderStatusName, statusChangedAt, statusNotes), most recent first.
    """
    result = _get("/list_orders")
    return result.get("orders", []) if isinstance(result, dict) else []
