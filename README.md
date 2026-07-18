# LoanAgents_SmartLoans

AI Loan Agents for SmartLoans, built with [Google ADK](https://google.github.io/adk-docs/). This
repo is completely independent from `smartloans_backend` — it never touches the database or
imports backend Python modules directly; it only talks to the backend over its public REST API
(see `tools/backend_api.py`).

Rather than simply matching borrowers and lenders, the AI becomes an intelligent negotiator that
increases the probability a loan closes while reducing the time required.

## Implemented

**Negotiation Agent** (`agents/negotiation/`) — sits inside a borrower↔lender loanChat
conversation. Given the latest chat message, it calls back into `smartloans_backend` to read the
conversation's terms, the borrower's existing loans, dashboard summary, and credit score, then
produces a grounded, concrete negotiation suggestion (never inventing numbers).

Exposed via `POST /negotiate` (`main.py`):
```json
{ "conversationId": 5, "borrowerId": 2116, "companyId": 1008, "message": "¿Puedo bajar la tasa?", "speakerRole": "borrower" }
→ { "reply": "..." }
```

`smartloans_backend/modules/loanChat.py` calls this endpoint whenever a borrower or lender messages
the reserved "Asistente SmartLoans" conversation counterpart, and inserts the returned reply back
into the chat.

## Running locally

```bash
pip install -e .
cp .env.example .env   # fill in GEMINI_API_KEY (or GCP_PROJECT + GCP_LOCATION for Vertex AI)
uvicorn main:app --reload --port 8080
```

## Repository layout

```
LoanAgents_SmartLoans/
├── agents/
│   └── negotiation/
│       ├── agent.py      # ADK Agent definition
│       └── prompt.py     # system instruction
├── tools/
│   └── backend_api.py    # HTTP calls into smartloans_backend's public API
├── config/
│   └── settings.py       # env var loading
├── tests/
│   └── test_negotiation_agent.py
├── main.py                # FastAPI app exposing POST /negotiate
└── pyproject.toml
```

## Roadmap — future ADK agents

The Negotiation Agent is the first of what can grow into a multi-agent ecosystem, each added
incrementally once the previous one is proven in production:

| Agent | Purpose |
|---|---|
| Risk Agent | Reads Smart Score, income, debt ratio, employment, payment history → recommends risk tier + interest rate |
| Recommendation Agent | Suggests alternative amount/rate/term combinations with higher approval probability |
| Borrower Agent | Represents the borrower's goals (lower interest, longer term, lower payment, faster approval) |
| Lender Agent | Represents the lender's goals (maximize ROI, reduce default risk, protect capital) |
| Contract Agent | After agreement, prepares the loan summary, contract variables, payment schedule, and digital signature package |
| Orchestrator Agent | Coordinates all of the above: Borrower → Negotiation → Risk → Recommendation → Lender → Contract |
| Fraud Detection Agent | Detects suspicious identities, devices, or transactions |
| Collections Agent | Monitors overdue payments and proposes recovery actions |
| Customer Support Agent | Answers questions and guides users through the platform |
| Investment Advisor Agent | Recommends lending opportunities to maximize lender returns |
| Portfolio Agent | Helps lenders diversify and monitor their loan portfolio |
| Compliance Agent | Verifies regulatory and AML/KYC compliance |
| Document Agent | Validates contracts and required documentation |
| Notification Agent | Determines optimal timing and audience for push notifications |

This architecture keeps the FastAPI backend focused on business logic, while this repo becomes an
intelligent orchestration layer that assists users throughout the lending lifecycle — and makes it
easy to add new specialized agents without touching the core SmartLoans application.
