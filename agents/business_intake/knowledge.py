"""
Paths and loaders for the knowledge the Business Intake agent reads at run time
(LoanAgents_SmartLoans/knowledge/). GMO_MODULES.md is maintained in this repo;
everything else is a copy of the GMO Factory's knowledge (Agent_POSGMO),
refreshed with scripts/sync_factory_knowledge.py.
"""
from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Set

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"
MODULES_MD = KNOWLEDGE / "GMO_MODULES.md"
STRUCTURE_CSV = KNOWLEDGE / "schema" / "structure_database.csv"
RELATIONSHIPS_JSON = KNOWLEDGE / "schema" / "sql_relationships.json"
DESIGNS = KNOWLEDGE / "designs"


@lru_cache(maxsize=1)
def load_schema_tables() -> Set[str]:
    """Distinct table names in the schema snapshot."""
    if not STRUCTURE_CSV.exists():
        return set()
    with open(STRUCTURE_CSV, encoding="utf-8-sig") as f:
        return {row[1] for row in csv.reader(f) if len(row) >= 2 and row[1]}


@lru_cache(maxsize=1)
def _relationships() -> List[dict]:
    return json.loads(RELATIONSHIPS_JSON.read_text(encoding="utf-8")) if RELATIONSHIPS_JSON.exists() else []


def neighbors_of(table: str) -> Dict[str, List[str]]:
    """1-hop FK neighbourhood: what this table is a child of, and what is a child of it."""
    rels = _relationships()
    return {
        "parents": sorted({r["parent_table"] for r in rels if r["child_table"] == table}),
        "children": sorted({r["child_table"] for r in rels if r["parent_table"] == table}),
    }


@lru_cache(maxsize=1)
def backend_routes() -> List[str]:
    path = KNOWLEDGE / "backend_routes.json"
    try:
        return [r["path"] for r in json.loads(path.read_text(encoding="utf-8"))["routes"]]
    except Exception:
        return []


@lru_cache(maxsize=1)
def stored_procedures() -> List[str]:
    path = KNOWLEDGE / "backend_stored_procedures.json"
    try:
        return [p["name"] for p in json.loads(path.read_text(encoding="utf-8"))["stored_procedures"]]
    except Exception:
        return []


@lru_cache(maxsize=1)
def verified_frontend_files() -> List[str]:
    path = KNOWLEDGE / "verified_frontend_files.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
