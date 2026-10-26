# LoanAgents_SmartLoans — repo rules

Adds to `~/.claude/CLAUDE.md` (global rules); this file wins on conflict.
AI agents (Google ADK + Gemini) behind a FastAPI app (`main.py`), used by
`smartloans_backend` and the POS. Deployed to Azure Web App `loanagents-smartloans`
by `.github/workflows/` — **only on push to `main`**; `development` is not deployed.

## 1. Boundaries
- This repo never touches the database and never imports backend code. It talks to
  `smartloans_backend` only over REST (`tools/backend_api.py`).
- Agents never write. A write is **proposed** with `propose_action`
  (`tools/pending_actions.py` validates the fields); the backend stores it and executes
  it only after the user's deterministic "sí" (`_classify_confirmation`). Never add a
  tool that writes, and never call backend write endpoints to "test".
- Trusted values (companyId, branchId, the sender's phone) are set by the route in
  session state / context. Tools read them from there — never as model-chosen args
  (see `get_my_reward_points`).

## 2. Adding or changing an agent
- Layout: `agents/<name>/agent.py` + `prompt.py`; export it in `agents/__init__.py`;
  expose it in `main.py` with **`MeteredRunner`**, never a plain `Runner` (token billing).
- Model: `gemini-2.5-flash` unless there's a measured reason.
- Read the reply from **this turn's events**, not `session.state[output_key]`: an empty
  model turn leaves the previous reply in state and the user gets it again
  (see `/support/whatsapp-reservations`).
- Dates: the route computes "today"/"nextDays" in Hermosillo time (UTC-7, no DST);
  prompts resolve relative dates only from that — the model never computes dates.

## 3. Prompts
- Business facts (services, prices, hours, promotions, points) come only from tools.
  Prompts stay business-agnostic: no company names, prices or hours in them.
- Customer-facing text: natural es-MX Spanish, short. WhatsApp: `*bold*`, `_italic_`,
  "• " lists — never a line starting with "* " or "- ", no markdown headings/tables/links.
- Sound like a person from the business, not a phone menu: ask one clarifying question
  when the request is vague, act in the same turn when it's clear, menus are optional.
- Change prompts with targeted rules inside the existing sections. Don't append long
  NEVER/MUST lists — they make the agent defensive and slower, and dilute the rules
  that matter.
- Only instruct what the model can actually see and do. Check the real flow first:
  e.g. on WhatsApp the backend answers "sí"/"no" to a pending proposal itself, and
  handoff/pause happen after the reply — the model never sees those.

## 4. Testing
- `python -m pytest -q tests/` before calling anything done.
- Prompt changes also need a **live run** against Gemini with `.env`: drive the route
  with FastAPI `TestClient` through the real conversations (read-only backend calls
  only), print each reply and its latency. The backend gives agents 30 s
  (`whatsappReservations._ask_agent`) — report the slowest turn.
- Gemini Flash is not deterministic: run the behavior you changed at least 3 times and
  report the hit rate, not one lucky run.
- Local `.env` has no `WORKER_KEY` → metering is off and `get_my_reward_points` returns
  "unavailable". That's local only; production needs it set.

## 5. Cross-repo flows to keep in mind
- WhatsApp: Meta webhook → `smartloans_backend/modules/whatsappCloud.py` →
  `whatsappReservations.reply_to` → `POST /support/whatsapp-reservations` here.
  Typing indicator, staff pause, sí/no, booking and push copies live in the backend.
- Agent calls are logged by the backend (`integrationLogs`, service
  `loanagents_smartloans`) — that's the data for the POS "Agente WhatsApp" dashboard.
