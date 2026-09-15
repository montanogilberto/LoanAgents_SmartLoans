"""
Semantic (embedding) search — the missing half of Hybrid Search from
Phase 3/4. retrieval/keyword_search.py is lexical-only by design (per the
architecture discussion's "don't start with embeddings everywhere");
this module is the vector half, added once the lexical half's real
limitation was demonstrated live (a Spanish query missing an
English-described route entirely — see retrieval/query_expansion.py's
docstring for the exact case).

Uses Gemini's gemini-embedding-001 (verified live 2026-09-15 — the
previously-assumed 'text-embedding-004' model name is retired/404 on this
API version; gemini-embedding-001 is the current stable embedding model)
via the SAME google-genai client the ADK agents already depend on — no
new provider, no vector database. The corpus (272 backend API routes,
2026-09-15) is small enough for brute-force cosine similarity over an
in-process cache; a real vector store only becomes worth the complexity
at a corpus size this approach can't hold in memory, which this one is
nowhere near.

Real, live-tested limitation (not resolved by this module): short/sparse
document text (e.g. a two-sentence route description) embeds poorly and
doesn't separate cleanly from unrelated documents — richer document text
gives visibly better separation. This is a property of the corpus, not a
bug here; see the module's own testing notes in the conversation that
built it.
"""
from __future__ import annotations

import math
import time

from google import genai
from google.genai import types

from config.settings import GEMINI_API_KEY
from retrieval.keyword_search import _fetch_documents

_MODEL = "gemini-embedding-001"
_BATCH_SIZE = 100  # API hard limit: "at most 100 requests can be in one batch" (verified live)
_CACHE_TTL_SECONDS = 300  # same in-process tradeoff as keyword_search.py's document cache

_client = genai.Client(api_key=GEMINI_API_KEY)
_cache: dict = {"documents": None, "vectors": None, "fetchedAt": 0.0}


def _doc_text(doc: dict) -> str:
    return f"{doc['path']} {doc['summary']} {doc['description']}"


def _embed_documents(documents: list[dict]) -> list[list[float]]:
    vectors: list[list[float]] = []
    texts = [_doc_text(d) for d in documents]
    for i in range(0, len(texts), _BATCH_SIZE):
        chunk = texts[i:i + _BATCH_SIZE]
        resp = _client.models.embed_content(
            model=_MODEL, contents=chunk,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        vectors.extend(e.values for e in resp.embeddings)
    return vectors


def _cached_document_vectors() -> tuple[list[dict], list[list[float]]]:
    now = time.time()
    if _cache["documents"] is not None and (now - _cache["fetchedAt"]) < _CACHE_TTL_SECONDS:
        return _cache["documents"], _cache["vectors"]

    documents = _fetch_documents()
    vectors = _embed_documents(documents)
    _cache["documents"] = documents
    _cache["vectors"] = vectors
    _cache["fetchedAt"] = now
    return documents, vectors


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def semantic_search(query: str, top_k: int = 5) -> list[dict]:
    """Embedding-based search over the same real backend API documentation
    corpus as keyword_search.search_docs — finds conceptually related
    routes even when no literal word matches (e.g. "how are points
    credited" finding the rewards route without the word "reward"
    appearing verbatim), which pure keyword matching cannot do.

    Args:
        query: The cashier's question, as typed (any language the
            embedding model handles — no expand_query() needed here,
            embeddings already capture some cross-language/synonym
            similarity, unlike lexical matching).
        top_k: Max number of results to return.

    Returns:
        List of {"path", "method", "summary", "description", "score"}
        (cosine similarity, 0-1 range, highest first). Empty list only on
        a query embedding failure -- callers should treat that as "no
        signal from this pillar," not "nothing exists," and still trust
        keyword_search's results.
    """
    documents, vectors = _cached_document_vectors()
    query_resp = _client.models.embed_content(
        model=_MODEL, contents=[query],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    query_vec = query_resp.embeddings[0].values

    scored = [
        {**doc, "score": _cosine(query_vec, vec)}
        for doc, vec in zip(documents, vectors)
    ]
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:top_k]
