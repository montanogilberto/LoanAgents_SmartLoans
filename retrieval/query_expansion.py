"""
Query Expansion — closes the gap the architecture discussion marked
Essential (Section 4.1) and flagged with its own worked example: "add
rewards" should expand toward points, ledger, tickets, redemption,
clients, products.

Deliberately NOT an LLM call (no added latency/cost per search, no risk of
a hallucinated expansion term) and NOT a large invented ontology — this is
a small, real glossary directly motivated by a live, reproducible failure:
searching "crear cliente" (Spanish) missed /clients (English "Manage
client records...") entirely during Phase 3/4 testing. Every entry here
maps a term a Spanish-speaking cashier would actually type to the English
terms the real docs_description/*.txt files actually use — grounded in
the real corpus, not speculative synonyms for concepts GMO doesn't have.

Scoped to the domains the POS support agents actually cover today
(clients, income, expenses, rewards, accounting) — extend this the same
way retrieval/graph.py and retrieval/contracts.py are extended: add an
entry only once a real query needs it, not preemptively.
"""
from __future__ import annotations

import re

_GLOSSARY: dict[str, list[str]] = {
    # clients
    "cliente": ["client", "clients"],
    "clientes": ["client", "clients"],
    "crear": ["create", "insert"],
    "registrar": ["register", "create"],
    "nuevo": ["new"],
    # income / sales
    "ingreso": ["income", "sale"],
    "ingresos": ["income", "sales"],
    "venta": ["income", "sale"],
    "ventas": ["income", "sales"],
    "pago": ["payment"],
    "cobrar": ["charge", "payment"],
    # expenses
    "gasto": ["expense"],
    "gastos": ["expenses"],
    "proveedor": ["supplier"],
    "nomina": ["payroll"],
    "nómina": ["payroll"],
    "empleado": ["employee"],
    # rewards / loyalty
    "puntos": ["points", "reward", "rewards"],
    "punto": ["points", "reward"],
    "recompensa": ["reward", "rewards", "points"],
    "recompensas": ["rewards", "points"],
    "canjear": ["redeem", "redemption"],
    "canje": ["redeem", "redemption"],
    "ganar": ["earn"],
    "ganancia": ["earn", "earned"],
    "saldo": ["balance"],
    "lealtad": ["loyalty", "rewards"],
    # accounting
    "contabilidad": ["accounting", "ledger"],
    "cuenta": ["account"],
    "cuentas": ["accounts"],
    "balanza": ["trial balance", "balance"],
    # receipts / tickets
    "factura": ["receipt", "ticket"],
    "recibo": ["receipt", "ticket"],
    "ticket": ["receipt", "ticket"],
}

_TOKEN_RE = re.compile(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ0-9_]+")


def expand_query(query: str) -> list[str]:
    """Returns the original query plus any glossary-matched expansion
    terms, deduplicated, original first. Used to widen BOTH the keyword
    search (more terms to match against real doc text) and, secondarily,
    to give the semantic search a same-language anchor alongside the
    original phrasing.

    Args:
        query: The cashier's raw question.

    Returns:
        [query, expansion_term_1, expansion_term_2, ...] — just the
        original query if no glossary term matched.
    """
    terms = [query]
    seen_expansions: set[str] = set()
    for token in (t.lower() for t in _TOKEN_RE.findall(query)):
        for expansion in _GLOSSARY.get(token, []):
            if expansion not in seen_expansions:
                seen_expansions.add(expansion)
                terms.append(expansion)
    return terms
