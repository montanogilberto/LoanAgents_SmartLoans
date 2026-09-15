"""
Hybrid Search — the agent-facing entry point that ties Query Expansion,
lexical search, semantic search, and cross-pillar re-ranking together
into the one tool an agent actually calls. This is the real "Hybrid
Search" pattern from the architecture discussion (Section 4.1: "Combine
keyword/lexical search with vector similarity"), not just the lexical
half retrieval/keyword_search.py provides alone.

Pipeline: expand_query() widens the lexical query with real domain
synonyms (retrieval/query_expansion.py) -> search_docs() runs lexical
search against the expanded terms -> semantic_search() runs embedding
search against the ORIGINAL query (embeddings don't need expansion the
same way, and expanding them risks diluting the query vector) ->
reciprocal_rank_fusion() combines both ranked lists into one.

hybrid_search() is the function wired into the POS support agents as a
tool (replacing the earlier keyword-only search_docs tool) -- see
agents/pos_clients_support/agent.py etc.
"""
from __future__ import annotations

from retrieval.keyword_search import search_docs
from retrieval.query_expansion import expand_query
from retrieval.rerank import reciprocal_rank_fusion
from retrieval.semantic_search import semantic_search


def hybrid_search(query: str, top_k: int = 5) -> list[dict]:
    """Searches real backend API documentation using both lexical
    (keyword, expanded with domain synonyms) and semantic (embedding)
    retrieval, then combines both into one ranked list. Use this for
    conceptual "how/why" questions that have no specific entity to
    resolve — for a specific client/sale/balance, use the structured or
    graph tools instead, this is for documentation/concept questions.

    Args:
        query: The cashier's question, in their own words (Spanish or
            English — query expansion covers the common domain terms;
            semantic search itself tolerates paraphrase and some
            cross-language similarity beyond that).
        top_k: Max number of results to return.

    Returns:
        List of {"path", "method", "summary", "description", "rrfScore",
        "sources"} — "sources" says whether keyword search, semantic
        search, or both surfaced each result (both agreeing is a stronger
        signal). Empty list if nothing matched either pillar — say so
        plainly, don't guess an answer with no matching documentation.
    """
    expanded_query = " ".join(expand_query(query))
    keyword_results = search_docs(expanded_query, top_k=top_k * 2)

    try:
        semantic_results = semantic_search(query, top_k=top_k * 2)
    except Exception as e:
        # Best-effort: a semantic-search failure (e.g. embedding API
        # hiccup) shouldn't take down the whole tool -- fall back to
        # keyword-only results rather than erroring the agent's turn.
        print(f"[hybrid_search] semantic_search failed, falling back to keyword-only: {e}")
        semantic_results = []

    return reciprocal_rank_fusion(keyword_results, semantic_results, top_k=top_k)
