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


## Business Intake — GMO Ecosystem website ("Ask the Factory")

**Business Intake & Ecosystem Onboarding agent** (`agents/business_intake/`, endpoint in `intake_api.py`): a business owner describes their business on www.gmoecosystem.com, and the agent answers how that business can **operate inside GMO** as a company (tenant). It's analysis only and writes nothing.

- **Decisions per capability:** REUSE / CONFIGURE / EXTEND / CREATE / INVESTIGATE / NOT APPLICABLE.
- **Readiness per layer:** mobile / web / backend / database.
- **The rest of the answer:** an onboarding plan, the questions that change the plan, and the onboarding readiness.
- **Development requests:** only when GMO itself must be extended.

**What GMO already has** comes from `knowledge/GMO_MODULES.md`, maintained here: modules, agents and how each one is wired. Each module's wiring is verified against `knowledge/` before any model call:
- the schema snapshot;
- backend routes and stored procedures;
- the verified frontend files;
- draft designs.

Everything in `knowledge/` except `GMO_MODULES.md` is a copy of the GMO Factory's knowledge (`Agent_POSGMO`). Refresh it with:

```bash
python scripts/sync_factory_knowledge.py
```

**Cost:** a text question makes 1 model call, and a repeated one 0 (answers are cached until `GMO_MODULES.md` changes).

```
POST /intake/analyze
{ "text": "...", "url": "https://their-business.com", "language": "es", "visitorId": "v_…" }
```

**Website commercial core** (`site_commercial/`, under `/site`):

| Feature | Details |
|---|---|
| Visit counter | `POST /site/visit`, `GET /site/stats` |
| AI token allowance per visitor | `GET /site/usage`; HTTP 402 when used up |
| Leads (mini registration + contact form) | `POST /site/leads`; fields follow `knowledge/designs/prd_leadCapture.json` |
| Lead follow-up for the GMO team | `/site/admin/*` with header `X-Admin-Key` |

Data lives in SQLite under `SITE_DATA_DIR` (on Azure: `/home/data`).

| Setting | Purpose |
|---|---|
| `SITE_ADMIN_KEY` | Password of the follow-up admin (admin is off when unset) |
| `SITE_DATA_DIR` | Visits / leads database folder (Azure: `/home/data`) |
| `SITE_FREE_TOKENS` / `SITE_REGISTERED_TOKENS` | AI token allowance per visitor (default 40,000 / 400,000) |
| `SITE_IP_SALT` | Salt for the hashed IPs (raw IPs are never stored) |
| `INTAKE_RATE_LIMIT_PER_HOUR` | Analyses per IP per hour (default 20) |
| `FACTORY_INTAKE_MODEL` | Model for the intake agents (default `gemini-2.5-flash`) |

**Run locally** for the website: the website's dev server uses port 8080, so start this service on 8090:

```bash
PORT=8090 python main.py
```

Tests: `tests/test_business_intake.py` and `tests/test_site_commercial.py` (2 live tests are skipped without `GEMINI_API_KEY`).

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
