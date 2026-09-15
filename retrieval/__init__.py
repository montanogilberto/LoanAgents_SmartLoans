"""
GMO Retrieval Layer — a small, reusable retrieval interface for agents,
NOT scoped to POS (see each module's own docstring for why). Three real
pillars, matching the architecture discussion's "Structured Retrieval |
Graph Traversal | Semantic Retrieval -> Connected Context" diagram:

- Structured retrieval — direct lookups by id/filter. No dedicated module
  here; these are the plain functions in tools/backend_api.py
  (get_one_client, get_reward_balance, resolve_income_receipt, ...). They
  didn't need re-wrapping — ADK's FunctionTool already exposes them to an
  agent directly, and that IS the structured-retrieval contract.
- Graph traversal — retrieval/graph.py's GRAPH_SCHEMA: a declarative,
  verified-against-the-live-DB list of entity relationships (client ->
  reward_transactions -> originating_sale -> income_receipt, ...).
- Hybrid (lexical + semantic) retrieval — for conceptual "how/why"
  questions with no specific entity to resolve. Three cooperating
  modules, wired together by hybrid.py's hybrid_search() (the ONE
  function actually exposed to agents as a tool):
    - keyword_search.py — lexical/exact-term matching over the backend's
      live OpenAPI docs (sourced from docs_description/*.txt).
    - query_expansion.py — a small, real Spanish<->English domain
      glossary that widens the lexical query (added after live testing
      showed "crear cliente" missing /clients entirely — see that
      module's docstring for the exact failure case it fixes).
    - semantic_search.py — Gemini embedding similarity over the same doc
      corpus, catching conceptual matches keyword search can't.
    - rerank.py — Reciprocal Rank Fusion, combining both pillars' ranked
      lists into one.

There is no fourth "unifying dispatcher" class across ALL pillars on
purpose — ADK's own agent already IS the retrieval controller (Section
2.4/5.6 of the architecture discussion: "the agent decides what
information it needs"). An agent is handed structured/graph/hybrid tools
and its prompt explains when each applies; the LLM's own reasoning is the
"reusable interface," not a hand-rolled router. hybrid.py's internal
fusion of keyword+semantic is a real exception to that — those two are
fused BEFORE the agent sees them because ranking-then-combining is a
mechanical step, not a judgment call the LLM needs to make.
"""
