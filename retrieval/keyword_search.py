"""
Keyword (lexical) search — Phase 3/4 of the GMO Retrieval Layer.

Origin: GMO_Agentic_RAG_Factory_Conversation.docx, "Phase 3 — Add
lightweight retrieval": "don't start with embeddings everywhere... your
question isn't 'find documents about rewards,' structured retrieval is
probably more valuable initially." This module is deliberately the
LEXICAL half of "Hybrid Search" only — no vector DB, no embeddings. It
exists for the other kind of question: conceptual "how does X work" /
"what is Y" questions that structured lookups and graph traversal
(retrieval/graph.py) don't answer, because there's no specific entity to
resolve.

The corpus is NOT a new knowledge base to maintain — it's the backend's
own live OpenAPI schema (GET /openapi.json), whose per-route
`summary`/`description` fields are exactly the docs_description/*.txt
files already maintained in smartloans_backend (see e.g. docs_description/
rewards.txt, clients.txt, income.txt). Reuse before invention: this file
adds zero new documentation to write or keep in sync — it just searches
what already exists and is already real.
"""
from __future__ import annotations

import re
import time

import httpx

from config.settings import SMARTLOANS_BACKEND_URL

_TIMEOUT = 15.0
_CACHE_TTL_SECONDS = 300  # in-process only, same durability tradeoff already
                           # accepted elsewhere in this codebase (e.g. main.py's
                           # InMemorySessionService) -- a restart just re-fetches.

_cache: dict = {"documents": None, "fetchedAt": 0.0}


def _fetch_documents() -> list[dict]:
    """Flattens the backend's live OpenAPI paths into searchable documents.
    Cached in-process for _CACHE_TTL_SECONDS -- the API surface changes on
    deploys, not per-request, so re-fetching every call would be wasteful."""
    now = time.time()
    if _cache["documents"] is not None and (now - _cache["fetchedAt"]) < _CACHE_TTL_SECONDS:
        return _cache["documents"]

    resp = httpx.get(f"{SMARTLOANS_BACKEND_URL}/openapi.json", timeout=_TIMEOUT)
    resp.raise_for_status()
    spec = resp.json()

    documents = []
    for path, methods in (spec.get("paths") or {}).items():
        for method, op in (methods or {}).items():
            if not isinstance(op, dict):
                continue
            documents.append({
                "path": path,
                "method": method.upper(),
                "summary": op.get("summary") or "",
                "description": op.get("description") or "",
            })

    _cache["documents"] = documents
    _cache["fetchedAt"] = now
    return documents


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def search_docs(query: str, top_k: int = 5) -> list[dict]:
    """Keyword-searches real backend API documentation (route summaries +
    descriptions, sourced from docs_description/*.txt) for conceptual
    questions a structured lookup or graph traversal can't answer — e.g.
    "how does the POS calculate rewards", "what does an EARN transaction
    represent", "what is the endpoint for creating a client".

    This is lexical/exact-term matching only (no semantic understanding)
    — it rewards exact identifier matches (sp_income, incomeId, a route
    path) over loose paraphrase, which is the right tool for finding real
    API/SP names, same rationale as Hybrid Search's keyword half in the
    GMO retrieval architecture discussion.

    Args:
        query: The cashier's question or a short set of keywords.
        top_k: Max number of matching routes to return.

    Returns:
        List of {"path", "method", "summary", "description", "score"},
        highest score first. Empty list if nothing matched -- say so
        plainly, don't guess an answer with no matching documentation.
    """
    tokens = _tokenize(query)
    if not tokens:
        return []

    documents = _fetch_documents()
    scored = []
    for doc in documents:
        path_tokens = set(_tokenize(doc["path"]))
        summary_tokens = _tokenize(doc["summary"])
        description_tokens = _tokenize(doc["description"])

        score = 0.0
        for tok in tokens:
            if tok in path_tokens:
                score += 3.0  # exact identifier/route match — the strongest signal
            score += 2.0 * summary_tokens.count(tok)
            score += 1.0 * description_tokens.count(tok)

        if score > 0:
            scored.append({**doc, "score": score})

    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:top_k]
