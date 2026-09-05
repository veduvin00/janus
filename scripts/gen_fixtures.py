"""
gen_fixtures.py — regenerate fixtures/ from the live engine (no LLM, deterministic).

    python scripts/gen_fixtures.py

Run after any change to an engine, build.py, or schema.py so the committed
fixtures stay in sync with what the API actually serves.
"""
from __future__ import annotations
import json
import os

from src.ingest import get_store
from src.build import build_client_insights
from src.engine.prioritise import build_triage

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
DEMO_CLIENTS = ["CL-0002", "CL-0012", "CL-0014", "CL-0019"]


def main():
    store = get_store()
    os.makedirs(_FIXTURES_DIR, exist_ok=True)
    for cid in DEMO_CLIENTS:
        insights = [i.to_dict() for i in build_client_insights(store, cid, narrate_llm=False)]
        path = os.path.join(_FIXTURES_DIR, f"insights_{cid}.json")
        with open(path, "w") as f:
            json.dump(insights, f, indent=2)
        print(f"wrote {path} ({len(insights)} insights)")

    triage = build_triage(store, narrate_llm=False)
    path = os.path.join(_FIXTURES_DIR, "triage.json")
    with open(path, "w") as f:
        json.dump(triage, f, indent=2)
    print(f"wrote {path} ({len(triage)} clients)")


if __name__ == "__main__":
    main()
