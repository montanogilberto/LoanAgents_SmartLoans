"""
GMO Relationship Graph — Phase 1 of the GMO Retrieval Layer.

Origin: GMO_Agentic_RAG_Factory_Conversation.docx, "Phase 1 — Lightweight
Knowledge Graph": "Don't build a giant GraphRAG platform. Start with a
relationship map over existing GMO entities... The first graph doesn't
even necessarily need a sophisticated graph database. You can initially
represent relationships through controlled queries/tools. The important
thing is establishing the graph reasoning contract."

This is exactly that: a declarative list of real, verified edges between
GMO entities, each backed by an existing tools/backend_api.py call — no
graph database, no embeddings, no caching. Every edge below was checked
against the live production schema (OBJECT_DEFINITION / sys.columns on
smartloans_backend's DB, 2026-09-15) before being added here — this file
should never describe an aspirational relationship, only a real one.

Deliberately NOT named/scoped as "pos_graph" or anything POS-specific —
this is meant to be a shared piece of THIS repo's (LoanAgents_SmartLoans)
retrieval layer, with the POS support agents as its consumer (see
agents/pos_clients_support/). This is scoped to runtime/domain agents
that answer questions using live data — it is NOT the "System Truth"
layer for Agent_POSGMO/posgmo-factory (the separate software-factory
repo that generates code/SQL/PRs). That repo has its own, independent
schema intelligence (schema_analyst_agent, querying the live SQL Server
directly via MCP) and does not consume this module.

Usage: an agent does NOT call functions in this file directly as tools —
ADK's own function-calling loop already lets an LLM chain multiple
FunctionTools in one turn (call one, inspect the result, decide the next
call). What this file gives an agent's PROMPT is a stable, code-verified
description of which hops exist, via describe_schema() — so the prompt's
reasoning instructions can reference real edges instead of a hand-written,
driftable description of the schema.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tools import backend_api as api


@dataclass(frozen=True)
class Edge:
    """One traversable relationship: from_type --relation--> to_type,
    resolved by calling `resolver` (a tools/backend_api.py function)."""
    from_type: str
    relation: str
    to_type: str
    resolver: Callable
    description: str


# The graph reasoning contract. Keep this list small and real — add an
# edge only after verifying the underlying FK/column against the live DB
# (see each resolver's own docstring in tools/backend_api.py for what was
# checked and when).
GRAPH_SCHEMA: list[Edge] = [
    Edge(
        from_type="client", relation="profile", to_type="client_record",
        resolver=api.get_one_client,
        description="clients.clientId -> client record (name, phone, email, clientType)",
    ),
    Edge(
        from_type="client", relation="reward_balance", to_type="reward_balance",
        resolver=api.get_reward_balance,
        description="clients.clientId -> rewardPoints.clientId (current balance, lifetime earned/redeemed)",
    ),
    Edge(
        from_type="client", relation="reward_transactions", to_type="reward_transaction[]",
        resolver=api.get_reward_transactions,
        description="clients.clientId -> rewardTransactions.clientId (earn/redeem ledger, most recent first)",
    ),
    Edge(
        from_type="reward_transaction", relation="originating_sale", to_type="income_receipt",
        resolver=api.resolve_income_receipt,
        description=(
            "rewardTransactions.referenceId -> income.incomeId. Only populated for "
            "transactions created after 2026-09-15 (see modules.rewards.earn_points_for_income "
            "in smartloans_backend) — older/seed rows may have a non-numeric or absent "
            "referenceId, meaning this hop has nothing real to resolve."
        ),
    ),
    Edge(
        from_type="income_receipt", relation="products", to_type="product[]",
        resolver=None,  # already embedded in resolve_income_receipt's "products" field — no separate call needed
        description="income.incomeId -> incomeDetails -> productId (line items; returned inline by resolve_income_receipt)",
    ),
    Edge(
        from_type="income_receipt", relation="client", to_type="client_record",
        resolver=None,  # already embedded in resolve_income_receipt's "client" field
        description="income.clientId -> client record (returned inline by resolve_income_receipt)",
    ),
]


def relations_from(entity_type: str) -> list[Edge]:
    """All outgoing edges from a given entity type — what can be
    traversed next after resolving a node of this type."""
    return [e for e in GRAPH_SCHEMA if e.from_type == entity_type]


def describe_schema() -> str:
    """Renders GRAPH_SCHEMA as short lines for a system prompt, so an
    agent's reasoning instructions stay in sync with the real schema
    instead of independently hand-maintained prose."""
    lines = []
    for e in GRAPH_SCHEMA:
        via = f"call {e.resolver.__name__}()" if e.resolver else "included inline in the previous hop's result"
        lines.append(f"- {e.from_type} --{e.relation}--> {e.to_type} ({via}): {e.description}")
    return "\n".join(lines)
