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
- Keyword (lexical) retrieval — retrieval/keyword_search.py's
  search_docs(): searches the backend's own live OpenAPI docs (sourced
  from docs_description/*.txt) for conceptual "how/why" questions that
  have no specific entity to resolve. Deliberately lexical-only, no
  embeddings — see that module's docstring for why, and for its real,
  demonstrated precision limits (exact-term matching, no cross-language
  understanding).

There is no fourth "unifying dispatcher" class here on purpose — ADK's
own agent already IS the retrieval controller (Section 2.4/5.6 of the
architecture discussion: "the agent decides what information it needs").
An agent is handed tools from all three pillars and its prompt explains
when each applies; the LLM's own reasoning is the "reusable interface,"
not a hand-rolled router. Building one would be exactly the "giant
platform" the architecture discussion says to avoid.
"""
