"""
Refreshes knowledge/ from the GMO Factory repo (Agent_POSGMO), which owns the
schema snapshot, the backend route / stored-procedure lists, the verified
frontend → backend chains and the draft module designs.

The Business Intake agent (agents/business_intake/) reads these files at run
time. This repo deploys on its own, so it keeps a committed copy instead of
reaching into another repo — run this after the Factory's knowledge changes:

    python scripts/sync_factory_knowledge.py [--factory ../Agent_POSGMO]

knowledge/GMO_MODULES.md is NOT synced: it is maintained here.
"""
from __future__ import annotations

import argparse
import ast
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "knowledge"


def verified_frontend_files(graph_py: Path) -> list[str]:
    """MODULES_IN_SCOPE from posgmo-factory/ecosystem_graph/graph.py, read without importing it."""
    tree = ast.parse(graph_py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = getattr(node, "target", None) or (node.targets[0] if isinstance(node, ast.Assign) else None)
        if getattr(target, "id", None) == "MODULES_IN_SCOPE":
            scope = ast.literal_eval(node.value)
            return sorted({f for files in scope.values() for f in files})
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factory", default=str(ROOT.parent / "Agent_POSGMO"), help="path to the Agent_POSGMO repo")
    factory = Path(parser.parse_args().factory).resolve()

    copies = {
        factory / "Database" / "structure_database.csv": KNOWLEDGE / "schema" / "structure_database.csv",
        factory / "Database" / "sql_relationships.json": KNOWLEDGE / "schema" / "sql_relationships.json",
        factory / "Backend" / "backend_routes.json.json": KNOWLEDGE / "backend_routes.json",
        factory / "Backend" / "backend_stored_procedures.json.json": KNOWLEDGE / "backend_stored_procedures.json",
    }
    for src, dst in copies.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"copied {src.relative_to(factory)} → {dst.relative_to(ROOT)}")

    files = verified_frontend_files(factory / "posgmo-factory" / "ecosystem_graph" / "graph.py")
    (KNOWLEDGE / "verified_frontend_files.json").write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")
    print(f"wrote knowledge/verified_frontend_files.json ({len(files)} files)")

    designs = KNOWLEDGE / "designs"
    designs.mkdir(exist_ok=True)
    for old in designs.glob("prd_*.json"):
        old.unlink()
    prds = sorted((factory / "posgmo-factory" / "tests").glob("prd_*.json"))
    for prd in prds:
        shutil.copyfile(prd, designs / prd.name)
    print(f"copied {len(prds)} draft designs → knowledge/designs/")


if __name__ == "__main__":
    main()
