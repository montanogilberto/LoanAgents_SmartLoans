"""
Calls into smartloans_backend's public REST API — this repo never touches
the database or imports backend Python modules directly, per the
independent-repo/API-only architecture.
"""
import httpx

from config.settings import SMARTLOANS_BACKEND_URL

_TIMEOUT = 15.0


def _post(path: str, body: dict) -> dict:
    resp = httpx.post(f"{SMARTLOANS_BACKEND_URL}{path}", json=body, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


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
    """Fetches this borrower's own loans (status, amount, rate, term).

    Args:
        client_id: The borrower's clientId.
        company_id: The company scoping the loans.

    Returns:
        List of loan records belonging to this client only.
    """
    result = _post("/all_loans", {"loans": [{"companyId": company_id}]})
    loans = result.get("loans", []) if isinstance(result, dict) else []
    return [l for l in loans if l.get("clientId") == client_id]


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
