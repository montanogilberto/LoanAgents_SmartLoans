"""
Cross-pillar re-ranking — combines ranked candidate lists from multiple
retrieval pillars (keyword_search's lexical scores, semantic_search's
cosine similarities) into ONE ranked list. This is the "20 candidates ->
5 strongest" step the architecture discussion describes, applied across
pillars — retrieval/keyword_search.py's own weighted scoring is a
re-ranker too, but only WITHIN the lexical pillar; this is the missing
piece that combines pillars.

Uses Reciprocal Rank Fusion (RRF) rather than normalizing and averaging
raw scores: keyword scores (unbounded term-frequency sums) and cosine
similarities (0-1) live on incompatible scales, and naive normalization
(e.g. min-max per list) is unstable on short candidate lists. RRF sidesteps
this entirely by using each item's RANK within its own list, not its raw
score — a well-established, parameter-light technique for exactly this
problem, not a novel design.
"""
from __future__ import annotations

_RRF_K = 60  # standard constant from the original RRF paper; not tuned for
             # this corpus -- revisit only if real usage shows it's wrong.


def _doc_key(doc: dict) -> tuple:
    return (doc["path"], doc["method"])


def reciprocal_rank_fusion(*ranked_lists: list[dict], top_k: int = 5) -> list[dict]:
    """Fuses any number of already-ranked candidate lists (each a list of
    {"path", "method", "summary", "description", ...} in descending
    relevance order) into one ranked list, by RRF score:
    sum over lists of 1/(_RRF_K + rank_in_that_list).

    An item present in multiple lists (found by both keyword and semantic
    search) accumulates score from each — that's the point: agreement
    across pillars is itself a relevance signal.

    Args:
        *ranked_lists: One or more ranked candidate lists. A list a
            pillar couldn't produce (e.g. semantic_search failed) should
            be passed as [] — RRF degrades gracefully to whatever pillars
            did return something.
        top_k: Max number of fused results to return.

    Returns:
        List of {"path", "method", "summary", "description", "rrfScore",
        "sources": [...]}, highest rrfScore first. "sources" names which
        pillar(s) surfaced this candidate, so a caller/prompt can say
        "found via keyword+semantic" vs just one.
    """
    fused: dict[tuple, dict] = {}
    for list_index, ranked_list in enumerate(ranked_lists):
        source = ("keyword", "semantic")[list_index] if list_index < 2 else f"pillar_{list_index}"
        for rank, doc in enumerate(ranked_list):
            key = _doc_key(doc)
            if key not in fused:
                fused[key] = {
                    "path": doc["path"], "method": doc["method"],
                    "summary": doc["summary"], "description": doc["description"],
                    "rrfScore": 0.0, "sources": [],
                }
            fused[key]["rrfScore"] += 1.0 / (_RRF_K + rank + 1)
            fused[key]["sources"].append(source)

    results = list(fused.values())
    results.sort(key=lambda d: d["rrfScore"], reverse=True)
    return results[:top_k]
